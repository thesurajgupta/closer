"""The event handler.

One function, three triggers. It is invoked by the local scheduler, by an SQS
poll loop, or by an AgentCore Runtime invocation — the code is identical, which
is the point: moving CLOSER to AWS is a deployment change, not a rewrite.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from .. import clock, orchestrator
from ..models.enums import LoopStatus, Priority, RunTrigger
from ..observability.telemetry import TELEMETRY, configure_logging
from ..state import machine
from ..store.repository import Repo


def handle_event(event: dict[str, Any]) -> dict[str, Any]:
    """Entry point for every background trigger."""
    configure_logging()
    detail = event.get("detail-type") or event.get("detail_type") or "closer.discovery"
    TELEMETRY.emit("background_event", {"detail_type": detail})

    if detail == "closer.discovery":
        run = orchestrator.run_once(RunTrigger.SCHEDULED_DISCOVERY)
        return _summary(run)

    if detail == "closer.follow_up":
        due = list(orchestrator.loops_due_for_check())
        if not due:
            return {"skipped": True, "reason": "nothing is due for a check"}
        run = orchestrator.run_once(RunTrigger.SCHEDULED_FOLLOW_UP)
        return _summary(run) | {"checked": [l.id for l in due]}

    if detail == "closer.deadline_sweep":
        return deadline_sweep()

    if detail == "closer.approval":
        plan_id = event.get("plan_id")
        return orchestrator.apply_decision(plan_id, event.get("decision", "approve"), event.get("choice"))

    raise ValueError(f"unknown event type: {detail}")


def deadline_sweep() -> dict[str, Any]:
    """Raise priority as deadlines approach and expire what has genuinely passed.

    This runs often and interrupts nobody: it only changes priority and state.
    Whether the user hears about any of it is still the Silence Engine's call.
    """
    raised, expired = [], []
    for loop in Repo.active_loops():
        days = clock.days_until(loop.deadline)
        if days is None:
            continue
        if days < -1 and loop.status not in (LoopStatus.HUMAN_DECISION, LoopStatus.WAITING):
            try:
                loop.status = machine.transition(loop.status, LoopStatus.EXPIRED)
                loop.resolution = "The deadline passed before this could be closed."
                expired.append(loop.id)
            except machine.IllegalTransition:
                continue
        elif days <= 3 and loop.priority is not Priority.URGENT:
            loop.priority = Priority.URGENT
            loop.next_check_at = clock.now() + timedelta(hours=6)
            raised.append(loop.id)
        elif days <= 7 and loop.priority is Priority.NORMAL:
            loop.priority = Priority.HIGH
            raised.append(loop.id)
        else:
            continue
        Repo.put_loop(loop)
    return {"raised": raised, "expired": expired}


def _summary(run) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "status": run.status.value,
        "discovered": run.loops_discovered,
        "advanced": run.loops_advanced,
        "decisions": run.decisions_required,
        "autonomous_actions": run.autonomous_actions,
        "failures": run.failures,
    }
