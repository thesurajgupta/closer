"""Action execution.

Everything dangerous about an autonomous agent lives here, so this module is
deliberately boring and defensive:

  * the policy engine is re-run at execution time, not trusted from plan time
  * every side effect is claimed under an idempotency key *before* it is
    attempted, so a crash between "did it" and "recorded it" cannot double-send
  * retries are bounded with exponential backoff and only for retryable errors
  * the model has no input here at all — dispatch is a fixed table
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any, Callable

from .. import clock
from ..agents import context as run_context
from ..config import get_settings
from ..connectors.base import ConnectorError, ConnectorResult
from ..connectors.registry import get_connectors
from ..ids import stable_id
from ..models.domain import ActionPlan, AuditEvent, OpenLoop
from ..models.enums import ActionStatus, ActionType, LoopStatus, PolicyDecision
from ..policy import engine
from ..state import machine
from ..store import db
from ..store.repository import Repo


class ExecutionRefused(RuntimeError):
    pass


def execute(plan: ActionPlan, approved_by: str | None = None) -> ActionPlan:
    ctx = run_context.current()
    loop = Repo.loop(plan.loop_id)
    if loop is None:
        raise ExecutionRefused(f"loop {plan.loop_id} vanished")

    # 1. Re-authorise. A plan approved an hour ago against different settings
    #    does not get to run under today's settings.
    evaluation = engine.evaluate(plan.action, engine.PolicyContext(
        loop=loop, settings=Repo.settings(), evidence_complete=loop.evidence.complete,
        confidence=loop.confidence,
        external_content_trusted=not Repo.is_processed(f"quarantine:{loop.source_ref}"),
    ))
    if evaluation.decision is PolicyDecision.DENY:
        return _block(plan, loop, "Policy refused this at execution time: " + " ".join(evaluation.reasons))
    if evaluation.decision is PolicyDecision.REQUIRE_APPROVAL and plan.approved_at is None:
        return _block(plan, loop, "This needs your approval and does not have it.")

    # 2. Claim the side effect. This is the crash barrier.
    claimed = db.claim_once(plan.idempotency_key, {
        "plan_id": plan.plan_id, "loop_id": plan.loop_id, "claimed_at": clock.now().isoformat(),
    })
    if not claimed:
        prior = db.get_claim(plan.idempotency_key) or {}
        if prior.get("result"):
            plan.status = ActionStatus.EXECUTED
            plan.execution_result = prior["result"]
            plan.executed_at = datetime.fromisoformat(prior.get("executed_at", clock.now().isoformat()))
            Repo.put_plan(plan)
            ctx.activity("This had already been done — did not repeat it", loop_id=loop.id)
            return plan
        # Claimed but never completed: a previous process died mid-flight.
        ctx.activity("Found an unfinished attempt from an earlier run and resumed it", loop_id=loop.id)

    plan.status = ActionStatus.EXECUTING
    loop.status = machine.transition(loop.status, LoopStatus.AUTO_EXECUTING)
    Repo.put_plan(plan)
    Repo.put_loop(loop)

    # 3. Run it, with bounded retries.
    settings = get_settings()
    last_error: Exception | None = None
    for attempt in range(1, settings.max_retries + 1):
        plan.attempt_count = attempt
        try:
            result = _dispatch(plan, loop)
            plan.status = ActionStatus.EXECUTED
            plan.executed_at = clock.now()
            plan.execution_result = {
                "external_ref": result.external_ref, "detail": result.detail,
                "simulated": result.simulated, **result.data,
            }
            plan.last_error = None
            db.put("side_effects", plan.idempotency_key, {
                "plan_id": plan.plan_id, "loop_id": plan.loop_id,
                "claimed_at": clock.now().isoformat(), "executed_at": plan.executed_at.isoformat(),
                "result": plan.execution_result,
            })
            loop.status = machine.transition(loop.status, LoopStatus.VERIFYING)
            loop.attempt_count = attempt
            loop.add_audit(AuditEvent(
                id=stable_id("aud", plan.plan_id, "executed"), at=clock.now(), actor="closer",
                agent="executor", kind="executed", message=result.detail, run_id=ctx.run.run_id,
                data={"external_ref": result.external_ref, "simulated": result.simulated},
            ))
            Repo.put_plan(plan)
            Repo.put_loop(loop)
            ctx.run.autonomous_actions += 0 if approved_by else 1
            ctx.activity(result.detail, loop_id=loop.id)
            return plan
        except ConnectorError as exc:
            last_error = exc
            ctx.run.retries += 1
            if not exc.retryable or attempt == settings.max_retries:
                break
            backoff = min(2 ** (attempt - 1) * 0.05, 1.0)
            ctx.activity(f"Attempt {attempt} failed ({exc}); retrying", state="running", loop_id=loop.id)
            time.sleep(backoff)
        except Exception as exc:  # non-connector failure: do not retry blindly
            last_error = exc
            break

    plan.status = ActionStatus.FAILED
    plan.last_error = str(last_error)
    loop.status = machine.transition(loop.status, LoopStatus.FAILED)
    loop.resolution = None
    loop.add_audit(AuditEvent(
        id=stable_id("aud", plan.plan_id, "failed"), at=clock.now(), actor="closer", agent="executor",
        kind="failed", message=f"Couldn't complete this: {last_error}", run_id=ctx.run.run_id,
    ))
    loop.next_check_at = clock.now() + timedelta(hours=6)
    Repo.put_plan(plan)
    Repo.put_loop(loop)
    ctx.run.failures += 1
    ctx.activity(f"Couldn't complete this: {last_error}", state="failed", loop_id=loop.id)
    return plan


def _block(plan: ActionPlan, loop: OpenLoop, reason: str) -> ActionPlan:
    ctx = run_context.current()
    plan.status = ActionStatus.BLOCKED
    plan.last_error = reason
    loop.add_audit(AuditEvent(
        id=stable_id("aud", plan.plan_id, "blocked"), at=clock.now(), actor="closer", agent="policy",
        kind="blocked", message=reason, run_id=ctx.run.run_id,
    ))
    Repo.put_plan(plan)
    Repo.put_loop(loop)
    ctx.activity(reason, state="skipped", loop_id=loop.id)
    return plan


# --- dispatch table --------------------------------------------------------


def _dispatch(plan: ActionPlan, loop: OpenLoop) -> ConnectorResult:
    handler: Callable[[ActionPlan, OpenLoop], ConnectorResult] | None = _HANDLERS.get(plan.action.action_type)
    if handler is None:
        raise ExecutionRefused(f"no executor registered for {plan.action.action_type.value}")
    return handler(plan, loop)


def _no_side_effect(detail: str) -> ConnectorResult:
    return ConnectorResult(ok=True, external_ref="", detail=detail, simulated=False)


def _record_finding(plan: ActionPlan, loop: OpenLoop) -> ConnectorResult:
    loop.resolution = plan.action.summary
    Repo.put_loop(loop)
    return _no_side_effect(plan.action.summary)


def _prepare_draft(plan: ActionPlan, loop: OpenLoop) -> ConnectorResult:
    ctx = run_context.current()
    draft = ctx.scratch.get("drafts", {}).get(loop.id)
    if draft:
        db.put("outbox", f"draft_{loop.id}", {
            "id": f"draft_{loop.id}", "loop_id": loop.id, "subject": draft["subject"],
            "body": draft["body"], "status": "draft", "created_at": clock.now().isoformat(),
        })
    return _no_side_effect("Draft prepared and saved. Nothing sent.")


def _schedule_follow_up(plan: ActionPlan, loop: OpenLoop) -> ConnectorResult:
    days = float(plan.action.payload.get("check_in_days", 3))
    loop.next_check_at = clock.now() + timedelta(days=days)
    Repo.put_loop(loop)
    return _no_side_effect(f"Next check scheduled for {loop.next_check_at:%a %d %b}.")


def _send_message(plan: ActionPlan, loop: OpenLoop) -> ConnectorResult:
    ctx = run_context.current()
    draft = ctx.scratch.get("drafts", {}).get(loop.id) or {
        "subject": plan.action.summary, "body": plan.action.rationale,
    }
    return get_connectors().email.send(
        to=plan.action.target, subject=draft["subject"], body=draft["body"],
        thread_id=None, idem=plan.idempotency_key,
    )


def _submit_warranty_claim(plan: ActionPlan, loop: OpenLoop) -> ConnectorResult:
    return get_connectors().warranty.submit_claim(
        merchant=plan.action.target, payload=plan.action.payload, idem=plan.idempotency_key,
    )


def _submit_billing_dispute(plan: ActionPlan, loop: OpenLoop) -> ConnectorResult:
    return get_connectors().billing.dispute(
        provider=plan.action.target, invoice_ref=str(plan.action.payload.get("invoice", "")),
        amount=plan.action.amount, reason=str(plan.action.payload.get("reason", "")),
        idem=plan.idempotency_key,
    )


def _reschedule(plan: ActionPlan, loop: OpenLoop) -> ConnectorResult:
    chosen = plan.action.payload.get("chosen_slot")
    if loop.category.value == "DELIVERY":
        slot = datetime.fromisoformat(chosen) if chosen else clock.now() + timedelta(days=1)
        return get_connectors().delivery.book_redelivery(
            tracking=str(plan.action.payload.get("tracking", "")), slot=slot, idem=plan.idempotency_key,
        )
    if not chosen:
        raise ExecutionRefused("no slot was chosen for this reschedule")
    event_id = next((i.source_id for i in loop.evidence.items if i.label == "Appointment"), loop.source_ref)
    return get_connectors().calendar.reschedule(event_id, datetime.fromisoformat(chosen), plan.idempotency_key)


def _confirm(plan: ActionPlan, loop: OpenLoop) -> ConnectorResult:
    event_id = next((i.source_id for i in loop.evidence.items if i.label == "Appointment"), loop.source_ref)
    return get_connectors().calendar.confirm(event_id, plan.idempotency_key)


def _upload(plan: ActionPlan, loop: OpenLoop) -> ConnectorResult:
    doc_id = next((i.source_id for i in loop.evidence.items if i.label == "Document to send"), None)
    if not doc_id:
        raise ExecutionRefused("no document selected for upload")
    reference = next((i.value for i in loop.evidence.items if i.label == "Reference"), loop.source_ref)
    return get_connectors().documents.upload(
        portal=plan.action.target, doc_id=doc_id, reference=reference, idem=plan.idempotency_key,
    )


_HANDLERS: dict[ActionType, Callable[[ActionPlan, OpenLoop], ConnectorResult]] = {
    ActionType.NO_ACTION: lambda p, l: _no_side_effect("Nothing to do."),
    ActionType.RECORD_FINDING: _record_finding,
    ActionType.ORGANIZE_DOCUMENT: lambda p, l: _no_side_effect("Filed."),
    ActionType.PREPARE_DRAFT: _prepare_draft,
    ActionType.SCHEDULE_FOLLOW_UP: _schedule_follow_up,
    ActionType.SEND_MESSAGE: _send_message,
    ActionType.SUBMIT_WARRANTY_CLAIM: _submit_warranty_claim,
    ActionType.SUBMIT_BILLING_DISPUTE: _submit_billing_dispute,
    ActionType.RESCHEDULE_APPOINTMENT: _reschedule,
    ActionType.CONFIRM_APPOINTMENT: _confirm,
    ActionType.UPLOAD_DOCUMENT: _upload,
}
