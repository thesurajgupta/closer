"""Impact metrics.

Every number here is derived from what actually happened in the store — loops
that really reached a state, plans that really executed, minutes attributed per
loop from a fixed table. Nothing is invented, and because the workload is
synthetic the API labels the whole block as illustrative.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from . import clock
from .models.enums import ActionStatus, LoopStatus, VerificationStatus
from .store.repository import Repo


def dashboard() -> dict[str, Any]:
    loops = Repo.loops()
    plans = Repo.plans()
    week_ago = clock.now() - timedelta(days=7)

    handled = [l for l in loops if l.status is LoopStatus.COMPLETED]
    waiting = [l for l in loops if l.status is LoopStatus.WAITING]
    decisions = [l for l in loops if l.status is LoopStatus.HUMAN_DECISION]
    failed = [l for l in loops if l.status is LoopStatus.FAILED]
    needs_you_now = [l for l in decisions if l.notify] + [l for l in failed if l.notify]
    held = [l for l in decisions if not l.notify]

    autonomous = [p for p in plans if p.approved_by is None
                  and p.status in (ActionStatus.EXECUTED, ActionStatus.VERIFIED)]
    approved = [p for p in plans if p.approved_by is not None
                and p.status in (ActionStatus.EXECUTED, ActionStatus.VERIFIED)]
    verified = [p for p in plans if p.verification
                and p.verification.status is VerificationStatus.CONFIRMED]

    minutes = sum(l.estimated_minutes_saved for l in loops
                  if l.status in (LoopStatus.COMPLETED, LoopStatus.WAITING, LoopStatus.HUMAN_DECISION))
    value = sum(l.value_at_stake for l in loops if l.status is not LoopStatus.CANCELLED)

    recent = [l for l in loops if (l.last_activity or l.created_at) >= week_ago]

    return {
        "handled_this_week": len([l for l in recent if l.status is LoopStatus.COMPLETED])
        + len([l for l in recent if l.status is LoopStatus.WAITING]),
        "closed": len(handled),
        "waiting_on_others": len(waiting),
        "needs_you": len(needs_you_now),
        "held_until_relevant": len(held),
        "failed": len(failed),
        "minutes_saved": minutes,
        "time_saved_label": _hm(minutes),
        "value_touched": round(value, 2),
        "currency": "INR",
        "autonomous_actions": len(autonomous),
        "human_decisions": len(approved) + len(decisions),
        "verified_actions": len(verified),
        "total_loops": len(loops),
        "interruption_rate": round(len(needs_you_now) / len(loops), 3) if loops else 0.0,
        "synthetic": True,
        "disclaimer": "Illustrative impact from a synthetic workload. No real accounts were contacted.",
    }


def _hm(minutes: int) -> str:
    hours, mins = divmod(int(minutes), 60)
    if hours and mins:
        return f"{hours}h {mins}m"
    if hours:
        return f"{hours}h"
    return f"{mins}m"


def run_report(run_id: str) -> dict[str, Any]:
    """The downloadable evidence bundle for one run."""
    from .store import db

    run = Repo.run(run_id)
    if not run:
        return {}
    loops = Repo.loops()
    plans = [p for p in Repo.plans() if p.run_id == run_id]
    return {
        "report": "CLOSER run evidence",
        "generated_at": clock.now().isoformat(),
        "data_notice": "All records are synthetic. No real person, company or account is involved.",
        "run": run.model_dump(mode="json"),
        "counts": {
            "input_events": run.events_scanned,
            "ignored": run.events_ignored,
            "duplicates_suppressed": run.duplicates_suppressed,
            "injection_attempts_blocked": run.injection_attempts_blocked,
            "loops_discovered": run.loops_discovered,
            "loops_advanced": run.loops_advanced,
            "completed": run.loops_completed,
            "waiting": run.loops_waiting,
            "human_decisions": run.decisions_required,
            "autonomous_actions": run.autonomous_actions,
            "failures": run.failures,
            "retries": run.retries,
            "tool_calls": len(run.tool_invocations),
        },
        "tool_invocations": [t.model_dump(mode="json") for t in run.tool_invocations],
        "activity": db.activity_for(run_id),
        "plans": [p.model_dump(mode="json") for p in plans],
        "loops": [l.model_dump(mode="json") for l in loops],
    }
