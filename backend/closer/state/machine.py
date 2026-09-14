"""The loop state machine.

This is deliberately *not* something the model can drive. Agents return
structured findings; only `transition()` changes a loop's status, and it refuses
any edge that is not in the table below. If a model hallucinated
"status: COMPLETED", nothing would happen.
"""

from __future__ import annotations

from ..models.enums import LoopStatus as S

ALLOWED: dict[S, set[S]] = {
    S.DISCOVERED: {S.UNDERSTANDING, S.CANCELLED, S.EXPIRED},
    S.UNDERSTANDING: {S.EVIDENCE_NEEDED, S.READY, S.WAITING, S.HUMAN_DECISION, S.CANCELLED, S.EXPIRED, S.FAILED},
    S.EVIDENCE_NEEDED: {S.UNDERSTANDING, S.READY, S.HUMAN_DECISION, S.EXPIRED, S.CANCELLED, S.FAILED},
    S.READY: {S.AUTO_EXECUTING, S.HUMAN_DECISION, S.WAITING, S.EXPIRED, S.CANCELLED, S.FAILED},
    S.AUTO_EXECUTING: {S.VERIFYING, S.WAITING, S.FAILED, S.CANCELLED},
    S.HUMAN_DECISION: {S.AUTO_EXECUTING, S.READY, S.CANCELLED, S.EXPIRED, S.WAITING, S.FAILED},
    S.WAITING: {S.UNDERSTANDING, S.VERIFYING, S.READY, S.HUMAN_DECISION, S.EXPIRED, S.FAILED, S.COMPLETED},
    S.VERIFYING: {S.COMPLETED, S.WAITING, S.FAILED, S.READY, S.HUMAN_DECISION},
    S.COMPLETED: set(),
    S.FAILED: {S.READY, S.HUMAN_DECISION, S.CANCELLED},
    S.EXPIRED: {S.CANCELLED},
    S.CANCELLED: set(),
}


class IllegalTransition(ValueError):
    def __init__(self, current: S, target: S) -> None:
        super().__init__(f"illegal loop transition {current.value} -> {target.value}")
        self.current = current
        self.target = target


def can(current: S, target: S) -> bool:
    return target == current or target in ALLOWED[current]


def transition(current: S, target: S) -> S:
    if target == current:
        return current
    if target not in ALLOWED[current]:
        raise IllegalTransition(current, target)
    return target


def describe(status: S) -> str:
    return {
        S.DISCOVERED: "Found",
        S.UNDERSTANDING: "Investigating",
        S.EVIDENCE_NEEDED: "Missing evidence",
        S.READY: "Ready to act",
        S.AUTO_EXECUTING: "Acting",
        S.WAITING: "Waiting on them",
        S.HUMAN_DECISION: "Needs you",
        S.VERIFYING: "Verifying",
        S.COMPLETED: "Closed",
        S.FAILED: "Couldn't complete",
        S.EXPIRED: "Expired",
        S.CANCELLED: "Cancelled",
    }[status]
