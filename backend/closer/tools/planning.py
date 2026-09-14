"""Planning tools: turn a recommendation into a policy-gated plan.

This is the boundary between what the model wants and what CLOSER is allowed to
do. `submit_action_plan` is the only way a proposal becomes a plan, and it always
runs the policy engine. The engine's answer — not the model's opinion — decides
whether the plan is queued for autonomous execution or held for the user.
"""

from __future__ import annotations

from typing import Any

from strands import tool

from .. import clock
from ..agents import context as run_context
from ..ids import digest, idempotency_key, stable_id
from ..models.domain import ActionPlan, ApprovalRequest, AuditEvent
from ..models.enums import ActionStatus, ActionType, LoopStatus, PolicyDecision, Priority
from ..policy import engine
from ..state import machine
from ..store.repository import Repo
from ._base import err, instrumented, ok


@tool
@instrumented("Checking what I'm allowed to do")
def check_policy(loop_id: str) -> dict[str, Any]:
    """Ask the policy engine what would happen if the loop's recommended action
    were submitted, without submitting it.

    Args:
        loop_id: The loop identifier.
    """
    loop = Repo.loop(loop_id)
    if not loop or not loop.recommended_action:
        return err("No recommended action on this loop yet.")
    ev = engine.evaluate(loop.recommended_action, engine.PolicyContext(
        loop=loop, settings=Repo.settings(), evidence_complete=loop.evidence.complete,
        confidence=loop.confidence,
    ))
    return ok(f"{ev.decision.value} ({ev.rule_id})", {
        "policy_decision": ev.decision.value, "policy_rule": ev.rule_id,
        "requires_approval": ev.requires_approval,
    }, reasons=ev.reasons)


@tool
@instrumented()
def submit_action_plan(loop_id: str) -> dict[str, Any]:
    """Submit the loop's recommended action as a plan. The policy engine decides
    whether it may run on its own or must wait for the user.

    Args:
        loop_id: The loop identifier.
    """
    ctx = run_context.current()
    loop = Repo.loop(loop_id)
    if not loop:
        return err(f"No loop with id '{loop_id}'.")
    action = loop.recommended_action
    if not action:
        return err("Nothing has been recommended for this loop yet.",
                   hint="Run the resolution step first.")

    trusted = not Repo.is_processed(f"quarantine:{loop.source_ref}")
    evaluation = engine.evaluate(action, engine.PolicyContext(
        loop=loop, settings=Repo.settings(), evidence_complete=loop.evidence.complete,
        confidence=loop.confidence, external_content_trusted=trusted,
    ))

    key = idempotency_key(loop.id, action.action_type.value, action.target, digest(action.payload))
    existing = Repo.plan_by_idempotency(key)
    if existing and existing.status not in (ActionStatus.REJECTED, ActionStatus.FAILED):
        ctx.activity("Recognised this plan already exists — not duplicating it", loop_id=loop_id)
        return ok(f"An identical plan already exists ({existing.plan_id}).", {
            "plan_id": existing.plan_id, "plan_status": existing.status.value,
            "policy_decision": existing.policy.decision.value, "plan_summary": existing.action.summary,
            "duplicate_plan": True,
        })

    plan = ActionPlan(
        plan_id=stable_id("plan", loop.id, action.action_type.value, ctx.run.run_id),
        loop_id=loop.id, run_id=ctx.run.run_id, action=action,
        risk_level=evaluation.risk_level, policy=evaluation,
        idempotency_key=key, created_at=clock.now(),
    )

    if evaluation.decision is PolicyDecision.DENY:
        plan.status = ActionStatus.BLOCKED
        loop.status = machine.transition(loop.status, LoopStatus.READY)
        loop.approval_required = False
        note = "Policy refused this: " + " ".join(evaluation.reasons)
    elif evaluation.decision is PolicyDecision.REQUIRE_APPROVAL:
        plan.status = ActionStatus.AWAITING_APPROVAL
        loop.status = machine.transition(loop.status, LoopStatus.HUMAN_DECISION)
        loop.approval_required = True
        Repo.put_approval(_approval_request(loop, plan))
        ctx.run.decisions_required += 1
        note = "Held for you: " + " ".join(evaluation.reasons)
    else:
        plan.status = ActionStatus.APPROVED  # authorised by policy, not by a person
        loop.status = machine.transition(loop.status, LoopStatus.READY)
        loop.approval_required = False
        note = "Cleared to run on its own: " + " ".join(evaluation.reasons)

    loop.risk_level = evaluation.risk_level
    loop.add_audit(AuditEvent(
        id=stable_id("aud", plan.plan_id, "policy"), at=clock.now(), actor="closer", agent="policy",
        kind="policy", message=note, run_id=ctx.run.run_id,
        data={"rule": evaluation.rule_id, "decision": evaluation.decision.value},
    ))
    Repo.put_plan(plan)
    Repo.put_loop(loop)
    ctx.activity(note, loop_id=loop_id)

    return ok(f"{evaluation.decision.value}: {action.summary}", {
        "plan_id": plan.plan_id, "plan_status": plan.status.value,
        "policy_decision": evaluation.decision.value, "policy_rule": evaluation.rule_id,
        "requires_approval": evaluation.requires_approval, "plan_summary": action.summary,
        "duplicate_plan": False,
    }, reasons=evaluation.reasons)


def _approval_request(loop, plan: ActionPlan) -> ApprovalRequest:
    """The decision card. It carries the whole investigation so the human only
    has to make the decision, never repeat the work."""
    found = []
    for item in loop.evidence.items:
        if len(found) >= 8:
            break
        # Don't repeat back to the reader what the card already said above.
        if item.value.strip() == loop.description.strip():
            continue
        suffix = "  (superseded — not used)" if item.stale else ""
        found.append(f"{item.label}: {item.value}{suffix}")
    if loop.evidence.missing:
        found.append("Still missing: " + ", ".join(loop.evidence.missing))

    consequences = _consequences(plan)
    choices = []
    payload_options = plan.action.payload.get("options") or []
    if payload_options:
        choices = [{"id": f"option_{i}", "label": str(option)} for i, option in enumerate(payload_options, 1)]

    return ApprovalRequest(
        plan_id=plan.plan_id, loop_id=loop.id, title=loop.title,
        what_happened=loop.description,
        what_i_found=found,
        recommendation=plan.action.summary,
        what_happens_if_approved=consequences,
        why_asking=plan.policy.approval_prompt or " ".join(plan.policy.reasons),
        evidence_ids=[i.id for i in loop.evidence.items],
        risk_level=plan.risk_level,
        choices=choices,
        deadline=loop.deadline,
        amount=plan.action.amount, currency=plan.action.currency,
    )


def _consequences(plan: ActionPlan) -> list[str]:
    a = plan.action
    base = {
        ActionType.SUBMIT_WARRANTY_CLAIM: [
            f"A warranty service request goes to {a.target} with your invoice and serial number.",
            "You get a claim reference back, and I'll chase it if they go quiet.",
        ],
        ActionType.SEND_MESSAGE: [
            f"One message is sent to {a.target}. Nothing else changes.",
            "I'll watch for the reply and follow up if none arrives.",
        ],
        ActionType.SUBMIT_BILLING_DISPUTE: [
            f"A reversal request for INR {a.amount:,.2f} is filed with {a.target} through their portal.",
            "It can be withdrawn any time before settlement.",
        ],
        ActionType.RESCHEDULE_APPOINTMENT: [
            "The appointment moves to the time you pick and your calendar is updated.",
            "I'll confirm the change actually took effect.",
        ],
        ActionType.CONFIRM_APPOINTMENT: ["The appointment is confirmed in your calendar."],
        ActionType.UPLOAD_DOCUMENT: [
            f"One document is shared with {a.target} through their portal.",
            "Nothing else from your files is shared.",
        ],
        ActionType.PREPARE_DRAFT: ["A draft is saved. Nothing is sent."],
        ActionType.RECORD_FINDING: ["The finding is recorded and the loop closes."],
        ActionType.SCHEDULE_FOLLOW_UP: ["I check back on this and act if nothing has changed."],
    }.get(a.action_type, [a.summary])
    return base


VERIFY_NOTE = "Verification runs after every side effect."
PLANNING_TOOLS = [check_policy, submit_action_plan]
