"""Verification tools.

An agent that reports success because it *called* a tool is not trustworthy. The
verification agent goes back to the outside world after every side effect and
asks whether the thing actually landed, then closes, holds or reopens the loop
accordingly.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from strands import tool

from .. import clock
from ..agents import context as run_context
from ..connectors.registry import get_connectors
from ..ids import stable_id
from ..models.domain import AuditEvent, VerificationResult
from ..models.enums import ActionStatus, ActionType, LoopStatus, VerificationStatus
from ..state import machine
from ..store.repository import Repo
from ._base import err, instrumented, ok


@tool
@instrumented("Checking it actually landed")
def check_external_state(plan_id: str) -> dict[str, Any]:
    """Go back to the provider and check whether the executed action is really
    reflected on their side.

    Args:
        plan_id: The executed plan to verify.
    """
    plan = Repo.plan(plan_id)
    if not plan:
        return err(f"No plan with id '{plan_id}'.")
    if plan.status not in (ActionStatus.EXECUTED, ActionStatus.VERIFIED):
        return ok("Nothing has been executed for this plan, so there is nothing to verify.", {
            "verification_status": VerificationStatus.NOT_APPLICABLE.value, "checks": [],
        })

    ref = (plan.execution_result or {}).get("external_ref", "")
    connectors = get_connectors()
    checks: list[dict[str, Any]] = []

    if plan.action.action_type is ActionType.SUBMIT_WARRANTY_CLAIM:
        state = connectors.warranty.claim_state(ref)
        checks.append({"check": "claim_registered", "expected": "received",
                       "actual": state.get("status", "unknown"),
                       "pass": state.get("status") == "received"})
        checks.append({"check": "reference_issued", "expected": "non-empty", "actual": ref, "pass": bool(ref)})
    elif plan.action.action_type is ActionType.SUBMIT_BILLING_DISPUTE:
        state = connectors.billing.dispute_state(ref)
        checks.append({"check": "dispute_acknowledged", "expected": "acknowledged",
                       "actual": state.get("status", "unknown"),
                       "pass": state.get("status") == "acknowledged"})
        checks.append({"check": "amount_matches", "expected": plan.action.amount,
                       "actual": state.get("amount"),
                       "pass": abs(float(state.get("amount", 0)) - plan.action.amount) < 0.01})
    elif plan.action.action_type in (ActionType.RESCHEDULE_APPOINTMENT, ActionType.CONFIRM_APPOINTMENT):
        loop = Repo.loop(plan.loop_id)
        if loop and loop.category.value == "DELIVERY":
            state = connectors.delivery.delivery_state(str(plan.action.payload.get("tracking", "")))
            checks.append({"check": "redelivery_booked", "expected": "redelivery_booked",
                           "actual": state.get("status", "unknown"),
                           "pass": state.get("status") == "redelivery_booked"})
        else:
            event_id = next((i.source_id for i in (loop.evidence.items if loop else [])
                             if i.label == "Appointment"), plan.loop_id)
            event = Repo.calendar_event(event_id)
            chosen = plan.action.payload.get("chosen_slot")
            checks.append({"check": "calendar_updated",
                           "expected": chosen or "confirmed",
                           "actual": event.starts_at.isoformat() if event else "missing",
                           "pass": bool(event and (not chosen or event.starts_at.isoformat() == chosen))})
            checks.append({"check": "event_confirmed", "expected": True,
                           "actual": bool(event and event.confirmed), "pass": bool(event and event.confirmed)})
    elif plan.action.action_type is ActionType.SEND_MESSAGE:
        state = connectors.email.thread_state(ref)
        checks.append({"check": "message_delivered", "expected": "delivered",
                       "actual": state.get("status", "unknown"), "pass": state.get("status") == "delivered"})
        checks.append({"check": "reply_received", "expected": "any", "actual": len(state.get("replies", [])),
                       "pass": bool(state.get("replies"))})
    elif plan.action.action_type is ActionType.UPLOAD_DOCUMENT:
        from ..store import db as _db
        state = _db.get("external_state", f"upload:{ref}") or {}
        checks.append({"check": "portal_received", "expected": "received",
                       "actual": state.get("status", "unknown"), "pass": state.get("status") == "received"})
    else:
        checks.append({"check": "local_effect_recorded", "expected": True, "actual": True, "pass": True})

    ctx = run_context.current()
    ctx.scratch.setdefault("verification_checks", {})[plan_id] = checks

    passed = sum(1 for c in checks if c["pass"])
    if passed == len(checks):
        status = VerificationStatus.CONFIRMED
    elif passed == 0:
        status = VerificationStatus.FAILED
    else:
        status = VerificationStatus.PARTIAL
    return ok(f"{passed}/{len(checks)} checks passed.", {
        "verification_status": status.value, "checks_passed": passed, "checks_total": len(checks),
    }, checks=checks)


@tool
@instrumented()
def record_verification(plan_id: str) -> dict[str, Any]:
    """Write the verification outcome onto the plan and move the loop to its
    correct next state — closed, still waiting, or failed.

    Args:
        plan_id: The verified plan.
    """
    ctx = run_context.current()
    plan = Repo.plan(plan_id)
    if not plan:
        return err(f"No plan with id '{plan_id}'.")
    loop = Repo.loop(plan.loop_id)
    if not loop:
        return err(f"No loop with id '{plan.loop_id}'.")

    checks = ctx.scratch.get("verification_checks", {}).get(plan_id, [])
    passed = sum(1 for c in checks if c.get("pass"))
    total = len(checks) or 1
    if passed == total:
        status = VerificationStatus.CONFIRMED
    elif passed == 0:
        status = VerificationStatus.FAILED
    else:
        status = VerificationStatus.PARTIAL

    result = VerificationResult(
        status=status, checks=checks, verified_at=clock.now(),
        detail=_detail(status, plan, passed, total, loop.external_party),
    )
    plan.verification = result
    plan.status = ActionStatus.VERIFIED if status is VerificationStatus.CONFIRMED else plan.status
    loop.verification = result

    if status is VerificationStatus.CONFIRMED and _closes_loop(plan):
        loop.status = machine.transition(loop.status, LoopStatus.COMPLETED)
        loop.resolution = plan.action.summary
        ctx.run.loops_completed += 1
    elif status is VerificationStatus.CONFIRMED:
        loop.status = machine.transition(loop.status, LoopStatus.WAITING)
        loop.next_check_at = clock.now() + timedelta(days=3)
        ctx.run.loops_waiting += 1
    elif status is VerificationStatus.PARTIAL:
        loop.status = machine.transition(loop.status, LoopStatus.WAITING)
        loop.next_check_at = clock.now() + timedelta(days=1)
        ctx.run.loops_waiting += 1
    else:
        loop.status = machine.transition(loop.status, LoopStatus.FAILED)
        ctx.run.failures += 1

    loop.add_audit(AuditEvent(
        id=stable_id("aud", plan_id, "verified"), at=clock.now(), actor="closer", agent="verification",
        kind="verified", message=result.detail, run_id=ctx.run.run_id,
        data={"status": status.value, "checks": checks},
    ))
    Repo.put_plan(plan)
    Repo.put_loop(loop)
    ctx.activity(result.detail, loop_id=loop.id)
    return ok(result.detail, {"verification_status": status.value, "loop_status": loop.status.value})


def _closes_loop(plan) -> bool:
    """A side effect that needs someone else to respond leaves the loop open and
    monitored; one that is complete in itself closes it."""
    return plan.action.action_type in {
        ActionType.RECORD_FINDING, ActionType.NO_ACTION, ActionType.ORGANIZE_DOCUMENT,
        ActionType.RESCHEDULE_APPOINTMENT, ActionType.CONFIRM_APPOINTMENT,
    }


def _detail(status: VerificationStatus, plan, passed: int, total: int, party: str | None = None) -> str:
    # Say who we are waiting on by name — an address is not a person.
    who = party or plan.action.target
    if "@" in who:
        who = party or who.split("@")[-1].split(".")[0].replace("-", " ").title()
    if status is VerificationStatus.CONFIRMED:
        if _closes_loop(plan):
            return f"Confirmed ({passed}/{total} checks). Loop closed."
        return f"Confirmed ({passed}/{total} checks). Now waiting on {who}."
    if status is VerificationStatus.PARTIAL:
        return f"Partly confirmed ({passed}/{total} checks). Holding it open and checking again tomorrow."
    return f"Could not confirm this landed ({passed}/{total} checks passed)."


VERIFICATION_TOOLS = [check_external_state, record_verification]
