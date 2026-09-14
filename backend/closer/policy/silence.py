"""The Silence Engine.

CLOSER's default is silence. Interrupting someone is a cost, so it has to be
earned. This module computes, deterministically, whether a loop's current state
justifies taking the user's attention — and, just as importantly, records *why*
it decided not to, so the user can audit CLOSER's restraint.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import clock
from ..models.domain import AutonomySettings, OpenLoop
from ..models.enums import LoopStatus, Priority, RiskLevel


@dataclass
class SilenceVerdict:
    notify: bool
    reason: str
    severity: str = "info"
    score: float = 0.0

    def as_tuple(self) -> tuple[bool, str]:
        return self.notify, self.reason


def evaluate(loop: OpenLoop, settings: AutonomySettings) -> SilenceVerdict:
    days_left = clock.days_until(loop.deadline)
    deadline_close = days_left is not None and days_left <= settings.notify_deadline_within_days
    overdue = days_left is not None and days_left < 0

    # --- always interrupt ---------------------------------------------------
    if loop.status is LoopStatus.FAILED:
        return SilenceVerdict(True, "I couldn't finish this and it needs you.", "failure", 1.0)
    if overdue and not loop.status.is_terminal:
        return SilenceVerdict(True, f"The deadline passed {abs(days_left):.0f} days ago.", "deadline", 1.0)

    if loop.status is LoopStatus.HUMAN_DECISION:
        # A decision exists — but a decision with plenty of runway is not
        # urgent. CLOSER holds it until it is close enough to matter.
        if loop.risk_level in (RiskLevel.FINANCIAL, RiskLevel.SENSITIVE) and \
                loop.value_at_stake > settings.notify_financial_above:
            return SilenceVerdict(True, f"A financial decision worth INR {loop.value_at_stake:,.0f}.",
                                  "decision", 0.95)
        if deadline_close or days_left is None:
            return SilenceVerdict(True, "This needs your judgement and I can't decide it for you.",
                                  "decision", 0.9)
        if loop.priority in (Priority.HIGH, Priority.URGENT):
            return SilenceVerdict(True, "This needs your judgement.", "decision", 0.85)
        return SilenceVerdict(
            False,
            f"Ready for you, but there are {days_left:.0f} days left — I'll bring it up nearer the deadline.",
            "info", 0.4,
        )

    if deadline_close and loop.status not in (LoopStatus.COMPLETED, LoopStatus.CANCELLED):
        if loop.priority in (Priority.HIGH, Priority.URGENT):
            return SilenceVerdict(True, f"{days_left:.0f} days left and still open.", "deadline", 0.8)

    # --- deliberately silent ------------------------------------------------
    if loop.status is LoopStatus.WAITING:
        return SilenceVerdict(False, f"Waiting on {loop.external_party or 'the other side'} — "
                                     "nothing for you to do yet.", "info", 0.1)
    if loop.status is LoopStatus.COMPLETED:
        if loop.value_at_stake >= settings.notify_financial_above * 2:
            return SilenceVerdict(False, "Closed. It'll show up in your weekly summary.", "info", 0.2)
        return SilenceVerdict(False, "Routine work, handled. No need to mention it.", "info", 0.05)
    if loop.status in (LoopStatus.AUTO_EXECUTING, LoopStatus.VERIFYING, LoopStatus.UNDERSTANDING,
                       LoopStatus.DISCOVERED, LoopStatus.READY, LoopStatus.EVIDENCE_NEEDED):
        return SilenceVerdict(False, "Still working on it.", "info", 0.1)
    if loop.status is LoopStatus.CANCELLED:
        return SilenceVerdict(False, "Nothing to do here.", "info", 0.0)

    return SilenceVerdict(False, "Nothing that needs you.", "info", 0.0)


def summarise(verdicts: dict[str, SilenceVerdict]) -> dict[str, int]:
    return {
        "notified": sum(1 for v in verdicts.values() if v.notify),
        "held_back": sum(1 for v in verdicts.values() if not v.notify),
    }
