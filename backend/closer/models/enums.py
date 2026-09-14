"""Enumerations shared across the CLOSER domain model.

These are deliberately *closed* sets. The LLM never invents a state or a risk
level; it can only propose values that already exist here, and the policy engine
is the only component allowed to grant execution authority.
"""

from __future__ import annotations

from enum import Enum


class LoopStatus(str, Enum):
    """Deterministic state machine for an open loop."""

    DISCOVERED = "DISCOVERED"
    UNDERSTANDING = "UNDERSTANDING"
    EVIDENCE_NEEDED = "EVIDENCE_NEEDED"
    READY = "READY"
    AUTO_EXECUTING = "AUTO_EXECUTING"
    WAITING = "WAITING"
    HUMAN_DECISION = "HUMAN_DECISION"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL


_TERMINAL = {
    LoopStatus.COMPLETED,
    LoopStatus.FAILED,
    LoopStatus.EXPIRED,
    LoopStatus.CANCELLED,
}


class LoopCategory(str, Enum):
    WARRANTY = "WARRANTY"
    BILLING = "BILLING"
    REFUND = "REFUND"
    APPOINTMENT = "APPOINTMENT"
    DOCUMENT_REQUEST = "DOCUMENT_REQUEST"
    RENEWAL = "RENEWAL"
    DELIVERY = "DELIVERY"
    OTHER = "OTHER"


class RiskLevel(str, Enum):
    """Risk classes drive the policy engine, not the model."""

    READ_ONLY = "READ_ONLY"
    LOW_RISK = "LOW_RISK"
    REVERSIBLE = "REVERSIBLE"
    EXTERNAL_COMMUNICATION = "EXTERNAL_COMMUNICATION"
    FINANCIAL = "FINANCIAL"
    SENSITIVE = "SENSITIVE"


RISK_ORDER = [
    RiskLevel.READ_ONLY,
    RiskLevel.LOW_RISK,
    RiskLevel.REVERSIBLE,
    RiskLevel.EXTERNAL_COMMUNICATION,
    RiskLevel.FINANCIAL,
    RiskLevel.SENSITIVE,
]


class ActionType(str, Enum):
    """The complete allowlist of things CLOSER is able to do.

    An action proposed by a model that is not in this list is rejected before it
    ever reaches the executor.
    """

    NO_ACTION = "NO_ACTION"
    ORGANIZE_DOCUMENT = "ORGANIZE_DOCUMENT"
    RECORD_FINDING = "RECORD_FINDING"
    PREPARE_DRAFT = "PREPARE_DRAFT"
    SCHEDULE_FOLLOW_UP = "SCHEDULE_FOLLOW_UP"
    SEND_MESSAGE = "SEND_MESSAGE"
    SUBMIT_WARRANTY_CLAIM = "SUBMIT_WARRANTY_CLAIM"
    SUBMIT_BILLING_DISPUTE = "SUBMIT_BILLING_DISPUTE"
    RESCHEDULE_APPOINTMENT = "RESCHEDULE_APPOINTMENT"
    CONFIRM_APPOINTMENT = "CONFIRM_APPOINTMENT"
    UPLOAD_DOCUMENT = "UPLOAD_DOCUMENT"
    CANCEL_SERVICE = "CANCEL_SERVICE"
    ISSUE_PAYMENT = "ISSUE_PAYMENT"


class ActionStatus(str, Enum):
    PROPOSED = "PROPOSED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"


class PolicyDecision(str, Enum):
    ALLOW_AUTONOMOUS = "ALLOW_AUTONOMOUS"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    DENY = "DENY"


class Priority(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


class VerificationStatus(str, Enum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class RunTrigger(str, Enum):
    MANUAL = "MANUAL"
    SCHEDULED_DISCOVERY = "SCHEDULED_DISCOVERY"
    SCHEDULED_FOLLOW_UP = "SCHEDULED_FOLLOW_UP"
    APPROVAL = "APPROVAL"
    EXTERNAL_RESPONSE = "EXTERNAL_RESPONSE"


class RunStatus(str, Enum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
