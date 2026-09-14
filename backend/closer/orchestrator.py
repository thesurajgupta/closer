"""The run engine.

A CLOSER run is: discover → understand → decide → authorise → act → verify →
decide whether to say anything. Each phase is driven by a real Strands agent;
this module sequences them, owns the run record, and is the only place the
executor and the Silence Engine are invoked.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Iterable

from . import clock
from .agents import context as run_context
from .agents.context import RunContext
from .agents.model_factory import provider_label
from .agents.specialists import intake_agent, supervisor_agent, verification_agent
from .execution.executor import execute
from .ids import sequence_id, stable_id
from .models.domain import AgentRun, AuditEvent, Notification, OpenLoop
from .models.enums import ActionStatus, LoopStatus, Priority, RunStatus, RunTrigger
from .observability.telemetry import TELEMETRY, configure_logging
from .policy import silence
from .state import machine
from .store import db
from .store.repository import Repo


def _new_run(trigger: RunTrigger) -> AgentRun:
    run_id = sequence_id("run")
    return AgentRun(
        run_id=run_id, session_id=stable_id("sess", clock.now().date().isoformat()),
        trigger=trigger, started_at=clock.now(), model_provider=provider_label(),
    )


def _inbound_items() -> list[str]:
    """Everything that could carry an open loop, in a stable order."""
    items = [m.id for m in Repo.messages()]
    items += [e.id for e in Repo.calendar() if not e.confirmed]
    items += [b.id for b in Repo.billing()
              if b.expected_amount is not None and abs(b.amount - b.expected_amount) > 1.0]
    return items


def discover(ctx: RunContext) -> None:
    """Phase 1 — look at everything new and decide what, if anything, it means."""
    ctx.agent = "intake"
    ctx.activity("Reading what's new", state="running")
    for item_id in _inbound_items():
        if Repo.is_processed(f"intake:{item_id}"):
            continue
        ctx.run.events_scanned += 1
        ctx.loop_id = None
        before = ctx.run.loops_discovered
        agent = intake_agent(item_id)
        result = agent(f"Process inbound item {item_id}.")
        Repo.mark_processed(f"intake:{item_id}", {
            "run_id": ctx.run.run_id, "at": clock.now().isoformat(), "outcome": str(result)[:200],
        })
        if ctx.run.loops_discovered == before:
            ctx.run.events_ignored += 1
    ctx.agent = "supervisor"
    ctx.loop_id = None
    ctx.activity(f"Read {ctx.run.events_scanned} items, {ctx.run.events_ignored} needed nothing")


def _needs_work(loop: OpenLoop) -> bool:
    if loop.status.is_terminal:
        return False
    if loop.status is LoopStatus.HUMAN_DECISION:
        return False
    if loop.status is LoopStatus.WAITING:
        return loop.next_check_at is not None and loop.next_check_at <= clock.now()
    if loop.status is LoopStatus.READY:
        # Already has a plan queued or held; re-running the supervisor would
        # just re-derive the same plan.
        live = [p for p in Repo.plans_for_loop(loop.id)
                if p.status in (ActionStatus.APPROVED, ActionStatus.AWAITING_APPROVAL,
                                ActionStatus.EXECUTING, ActionStatus.EXECUTED, ActionStatus.VERIFIED)]
        return not live
    return True


def advance(ctx: RunContext) -> None:
    """Phase 2 — for each loop that needs work, run the supervisor."""
    for loop in Repo.active_loops():
        if not _needs_work(loop):
            continue
        ctx.loop_id = loop.id
        ctx.agent = "supervisor"
        if loop.status is LoopStatus.DISCOVERED:
            loop.status = machine.transition(loop.status, LoopStatus.UNDERSTANDING)
            Repo.put_loop(loop)
        ctx.activity(f"Working on: {loop.title}", state="running", loop_id=loop.id)
        agent = supervisor_agent(loop.id)
        agent(f"Advance loop {loop.id} as far as you safely can.")
        ctx.run.loops_advanced += 1
    ctx.loop_id = None


def act(ctx: RunContext) -> None:
    """Phase 3 — run everything the policy engine authorised, then verify it."""
    for plan in Repo.plans():
        if plan.status is not ActionStatus.APPROVED:
            continue
        ctx.loop_id = plan.loop_id
        ctx.agent = "executor"
        executed = execute(plan)
        if executed.status is ActionStatus.EXECUTED:
            verify(ctx, executed.plan_id)
    ctx.loop_id = None
    ctx.agent = "supervisor"


def verify(ctx: RunContext, plan_id: str) -> None:
    """Phase 4 — a real check that the side effect landed."""
    plan = Repo.plan(plan_id)
    if not plan:
        return
    with run_context.acting_as("verification", plan.loop_id):
        agent = verification_agent(plan_id)
        agent(f"Verify plan {plan_id}.")


def escalate_and_quieten(ctx: RunContext) -> None:
    """Phase 5 — the Silence Engine. Decide, per loop, whether the user hears
    about any of this."""
    ctx.agent = "silence"
    settings = Repo.settings()
    for loop in Repo.loops():
        verdict = silence.evaluate(loop, settings)
        changed = loop.notify != verdict.notify or loop.notify_reason != verdict.reason
        loop.notify = verdict.notify
        loop.notify_reason = verdict.reason
        # Deadline pressure raises priority without raising noise.
        days = clock.days_until(loop.deadline)
        if days is not None and not loop.status.is_terminal:
            if days <= 3:
                loop.priority = Priority.URGENT
            elif days <= 7 and loop.priority is Priority.NORMAL:
                loop.priority = Priority.HIGH
        if changed:
            Repo.put_loop(loop)
        if verdict.notify:
            notification = Notification(
                id=stable_id("ntf", loop.id, verdict.severity), at=clock.now(), loop_id=loop.id,
                title=loop.title, body=verdict.reason, severity=verdict.severity,  # type: ignore[arg-type]
            )
            Repo.put_notification(notification)
    decisions = [l for l in Repo.loops() if l.status is LoopStatus.HUMAN_DECISION]
    notified = [l for l in Repo.loops() if l.notify]
    held = len(decisions) - len([l for l in decisions if l.notify])
    ctx.activity(
        f"{len(notified)} thing(s) worth interrupting you for"
        + (f"; {held} decision(s) held until they matter" if held else ""),
    )
    ctx.agent = "supervisor"


def finalise(ctx: RunContext) -> AgentRun:
    run = ctx.run
    loops = Repo.loops()
    run.loops_waiting = sum(1 for l in loops if l.status is LoopStatus.WAITING)
    run.decisions_required = sum(1 for l in loops if l.status is LoopStatus.HUMAN_DECISION)
    run.loops_completed = sum(1 for l in loops if l.status is LoopStatus.COMPLETED)
    run.minutes_saved = sum(l.estimated_minutes_saved for l in loops if l.status is not LoopStatus.DISCOVERED)
    run.value_touched = sum(l.value_at_stake for l in loops)
    run.finished_at = clock.now()
    run.status = RunStatus.COMPLETED
    Repo.put_run(run)
    return run


def run_once(trigger: RunTrigger = RunTrigger.MANUAL) -> AgentRun:
    """One complete CLOSER pass."""
    configure_logging()
    run = _new_run(trigger)
    Repo.put_run(run)
    ctx = RunContext(run=run)
    token = run_context.set_context(ctx)
    TELEMETRY.emit("run_started", {"run_id": run.run_id, "trigger": trigger.value,
                                   "model": run.model_provider})
    try:
        ctx.activity("CLOSER started", state="running")
        discover(ctx)
        advance(ctx)
        act(ctx)
        # A loop can become actionable *because* of what just happened
        # (evidence completed, a plan cleared). One bounded second pass catches
        # that without letting the run wander.
        advance(ctx)
        act(ctx)
        escalate_and_quieten(ctx)
        run = finalise(ctx)
        ctx.activity("CLOSER finished")
        TELEMETRY.emit("run_finished", run.model_dump(mode="json", exclude={"tool_invocations"}))
        return run
    except Exception as exc:
        run.status = RunStatus.FAILED
        run.finished_at = clock.now()
        run.notes.append(f"run failed: {exc}")
        Repo.put_run(run)
        ctx.activity(f"CLOSER stopped early: {exc}", state="failed")
        raise
    finally:
        run_context.reset_context(token)


# ---------------------------------------------------------------------------
# Approval — the human decision path
# ---------------------------------------------------------------------------


def apply_decision(plan_id: str, decision: str, choice: str | None = None,
                   edited_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Act on a human decision about one specific plan.

    Approval is bound to `plan_id` and to the plan's current state. A stale or
    already-resolved plan is refused rather than silently re-run.
    """
    configure_logging()
    plan = Repo.plan(plan_id)
    if not plan:
        return {"ok": False, "error": f"no plan {plan_id}"}
    if plan.status is not ActionStatus.AWAITING_APPROVAL:
        return {"ok": False, "error": f"plan {plan_id} is {plan.status.value}, not awaiting approval"}

    run = _new_run(RunTrigger.APPROVAL)
    Repo.put_run(run)
    ctx = RunContext(run=run, loop_id=plan.loop_id, agent="approval")
    token = run_context.set_context(ctx)
    try:
        loop = Repo.loop(plan.loop_id)
        if decision == "reject":
            plan.status = ActionStatus.REJECTED
            plan.rejected_at = clock.now()
            loop.status = machine.transition(loop.status, LoopStatus.CANCELLED)
            loop.resolution = "You decided not to go ahead."
            loop.notify = False
            loop.add_audit(AuditEvent(id=stable_id("aud", plan_id, "rejected"), at=clock.now(), actor="user",
                                      kind="rejected", message="You declined this.", run_id=run.run_id))
            Repo.put_plan(plan)
            Repo.put_loop(loop)
            ctx.activity("You declined this — closed it out", loop_id=loop.id)
            return {"ok": True, "status": plan.status.value, "loop_status": loop.status.value}

        if choice:
            plan.action.payload = {**plan.action.payload, "chosen_slot": choice}
        if edited_payload:
            plan.action.payload = {**plan.action.payload, **edited_payload}
        plan.status = ActionStatus.APPROVED
        plan.approved_at = clock.now()
        plan.approved_by = "user"
        loop.status = machine.transition(loop.status, LoopStatus.AUTO_EXECUTING)
        loop.approval_required = False
        loop.add_audit(AuditEvent(
            id=stable_id("aud", plan_id, "approved"), at=clock.now(), actor="user", kind="approved",
            message="You approved this" + (f" — {choice}" if choice else "."), run_id=run.run_id,
            data={"plan_id": plan_id, "risk": plan.risk_level.value, "choice": choice},
        ))
        Repo.put_plan(plan)
        Repo.put_loop(loop)
        ctx.activity("You approved it — carrying it out now", state="running", loop_id=loop.id)

        executed = execute(plan, approved_by="user")
        if executed.status is ActionStatus.EXECUTED:
            verify(ctx, plan_id)
        escalate_and_quieten(ctx)
        finalise(ctx)
        final_plan = Repo.plan(plan_id)
        final_loop = Repo.loop(plan.loop_id)
        return {
            "ok": final_plan.status in (ActionStatus.EXECUTED, ActionStatus.VERIFIED),
            "status": final_plan.status.value,
            "loop_status": final_loop.status.value,
            "result": final_plan.execution_result,
            "verification": final_plan.verification.model_dump(mode="json") if final_plan.verification else None,
            "run_id": run.run_id,
        }
    finally:
        run_context.reset_context(token)


def retry_failed(loop_id: str) -> dict[str, Any]:
    """Re-attempt the most recent failed plan on a loop."""
    plans = [p for p in Repo.plans_for_loop(loop_id) if p.status is ActionStatus.FAILED]
    if not plans:
        return {"ok": False, "error": "nothing failed on this loop"}
    plan = plans[-1]
    loop = Repo.loop(loop_id)
    run = _new_run(RunTrigger.SCHEDULED_FOLLOW_UP)
    Repo.put_run(run)
    ctx = RunContext(run=run, loop_id=loop_id, agent="executor")
    token = run_context.set_context(ctx)
    try:
        # A failed attempt released nothing, so the idempotency claim is cleared
        # before retrying — the side effect provably never landed.
        db.delete("side_effects", plan.idempotency_key)
        plan.status = ActionStatus.APPROVED
        loop.status = machine.transition(loop.status, LoopStatus.READY)
        Repo.put_plan(plan)
        Repo.put_loop(loop)
        executed = execute(plan, approved_by=plan.approved_by)
        if executed.status is ActionStatus.EXECUTED:
            verify(ctx, plan.plan_id)
        escalate_and_quieten(ctx)
        finalise(ctx)
        return {"ok": executed.status in (ActionStatus.EXECUTED, ActionStatus.VERIFIED),
                "status": Repo.plan(plan.plan_id).status.value, "run_id": run.run_id}
    finally:
        run_context.reset_context(token)


def simulate_external_response(loop_id: str, outcome: str = "resolved") -> dict[str, Any]:
    """Demo affordance: let a judge play the other side.

    A provider replying is the event that closes most real loops, so the demo
    needs to be able to produce one. It is clearly labelled as a simulated
    external event and goes through the same verification path as anything else.
    """
    loop = Repo.loop(loop_id)
    if not loop:
        return {"ok": False, "error": "no such loop"}
    run = _new_run(RunTrigger.EXTERNAL_RESPONSE)
    Repo.put_run(run)
    ctx = RunContext(run=run, loop_id=loop_id, agent="verification")
    token = run_context.set_context(ctx)
    try:
        plans = [p for p in Repo.plans_for_loop(loop_id)
                 if p.status in (ActionStatus.EXECUTED, ActionStatus.VERIFIED)]
        for plan in plans:
            ref = (plan.execution_result or {}).get("external_ref", "")
            for prefix in ("claim", "dispute", "thread", "upload"):
                key = f"{prefix}:{ref}"
                state = db.get("external_state", key)
                if state:
                    state["status"] = {"claim": "approved", "dispute": "settled",
                                       "thread": "delivered", "upload": "accepted"}[prefix]
                    state["replies"] = [{"at": clock.now().isoformat(), "outcome": outcome, "simulated": True}]
                    state["closed_at"] = clock.now().isoformat()
                    db.put("external_state", key, state)
        loop = Repo.loop(loop_id)
        if loop.status is LoopStatus.WAITING:
            loop.status = machine.transition(loop.status, LoopStatus.COMPLETED)
            loop.resolution = f"{loop.external_party or 'The provider'} came back: {outcome}."
            loop.add_audit(AuditEvent(
                id=stable_id("aud", loop_id, "external", clock.now().isoformat()), at=clock.now(),
                actor="external", kind="response",
                message=f"Simulated reply from {loop.external_party or 'the provider'}: {outcome}.",
                run_id=run.run_id, data={"simulated": True},
            ))
            Repo.put_loop(loop)
            ctx.activity(f"{loop.external_party or 'They'} responded — loop closed", loop_id=loop_id)
        escalate_and_quieten(ctx)
        finalise(ctx)
        return {"ok": True, "loop_status": Repo.loop(loop_id).status.value, "run_id": run.run_id}
    finally:
        run_context.reset_context(token)


def loops_due_for_check(now: Any = None) -> Iterable[OpenLoop]:
    moment = now or clock.now()
    return [l for l in Repo.active_loops() if l.next_check_at and l.next_check_at <= moment]
