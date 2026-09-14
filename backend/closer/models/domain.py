"""Typed contracts for CLOSER.

Everything that crosses a boundary — agent output, tool input, API response,
persisted row — is a Pydantic model with strict validation. Free-form text from
an LLM is never trusted as structure.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import (
    ActionStatus,
    ActionType,
    LoopCategory,
    LoopStatus,
    PolicyDecision,
    Priority,
    RiskLevel,
    RunStatus,
    RunTrigger,
    VerificationStatus,
)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False, validate_assignment=True)


# --------------------------------------------------------------------------
# Source material
# --------------------------------------------------------------------------


class DocumentField(Strict):
    """A single extracted fact, always carrying its source."""

    name: str
    value: str
    source_document_id: str
    source_locator: str = Field(description="Page/section/line reference inside the document.")
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)


class Document(Strict):
    id: str
    title: str
    doc_type: Literal[
        "receipt", "invoice", "warranty", "policy", "letter", "statement", "id_document", "report", "other"
    ]
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    entities: list[str] = Field(default_factory=list)
    text: str
    fields: dict[str, str] = Field(default_factory=dict)
    superseded_by: str | None = None
    tags: list[str] = Field(default_factory=list)

    @property
    def is_stale(self) -> bool:
        return self.superseded_by is not None


class InboxMessage(Strict):
    """An untrusted external message. Content is DATA, never instruction."""

    id: str
    sender: str
    sender_domain: str
    subject: str
    body: str
    received_at: datetime
    attachments: list[str] = Field(default_factory=list)
    thread_id: str | None = None
    external_ref: str | None = None


class CalendarEvent(Strict):
    id: str
    title: str
    starts_at: datetime
    ends_at: datetime
    location: str | None = None
    organizer: str | None = None
    importance: Literal["low", "normal", "high"] = "normal"
    confirmed: bool = True


class BillingRecord(Strict):
    id: str
    provider: str
    plan: str
    period: str
    amount: float
    currency: str = "INR"
    charged_at: datetime
    expected_amount: float | None = None
    document_id: str | None = None


class WarrantyRecord(Strict):
    id: str
    product: str
    serial: str
    merchant: str
    purchased_at: datetime
    months: int
    receipt_document_id: str | None = None
    warranty_document_id: str | None = None
    coverage_value: float = 0.0
    currency: str = "INR"


# --------------------------------------------------------------------------
# Evidence
# --------------------------------------------------------------------------


class EvidenceItem(Strict):
    """One traceable fact behind a conclusion. Every claim CLOSER makes in the UI
    resolves to one of these."""

    id: str
    key: str = ""
    label: str
    value: str
    source_type: Literal["document", "message", "calendar", "billing", "warranty", "derived"]
    source_id: str
    source_title: str
    locator: str = ""
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    stale: bool = False


class EvidenceBundle(Strict):
    items: list[EvidenceItem] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    complete: bool = False
    notes: str = ""

    def labels(self) -> list[str]:
        return [i.label for i in self.items]


# --------------------------------------------------------------------------
# Actions, policy, approval
# --------------------------------------------------------------------------


class ProposedAction(Strict):
    """What the model *proposes*. Carries no execution authority on its own."""

    action_type: ActionType
    summary: str
    rationale: str
    target: str = Field(description="External party or internal target of the action.")
    payload: dict[str, Any] = Field(default_factory=dict)
    amount: float = 0.0
    currency: str = "INR"
    evidence_ids: list[str] = Field(default_factory=list)
    reversible: bool = True

    @field_validator("summary", "rationale", "target")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be empty")
        return v.strip()


class PolicyEvaluation(Strict):
    decision: PolicyDecision
    risk_level: RiskLevel
    reasons: list[str] = Field(default_factory=list)
    rule_id: str
    requires_approval: bool
    approval_prompt: str = ""


class ActionPlan(Strict):
    """A proposed action after it has passed through the deterministic policy
    engine. `plan_id` + `idempotency_key` are what an approval binds to."""

    plan_id: str
    loop_id: str
    run_id: str
    action: ProposedAction
    risk_level: RiskLevel
    policy: PolicyEvaluation
    status: ActionStatus = ActionStatus.PROPOSED
    idempotency_key: str
    created_at: datetime
    approved_at: datetime | None = None
    approved_by: str | None = None
    rejected_at: datetime | None = None
    executed_at: datetime | None = None
    execution_result: dict[str, Any] | None = None
    verification: "VerificationResult | None" = None
    attempt_count: int = 0
    last_error: str | None = None


class ApprovalRequest(Strict):
    """The exact payload the human sees and signs off on."""

    plan_id: str
    loop_id: str
    title: str
    what_happened: str
    what_i_found: list[str]
    recommendation: str
    what_happens_if_approved: list[str]
    why_asking: str
    evidence_ids: list[str]
    risk_level: RiskLevel
    options: list[str] = Field(default_factory=lambda: ["approve", "edit", "reject"])
    choices: list[dict[str, str]] = Field(default_factory=list)
    deadline: datetime | None = None
    amount: float = 0.0
    currency: str = "INR"


class VerificationResult(Strict):
    status: VerificationStatus
    checks: list[dict[str, Any]] = Field(default_factory=list)
    detail: str = ""
    verified_at: datetime | None = None


# --------------------------------------------------------------------------
# The loop itself
# --------------------------------------------------------------------------


class AuditEvent(Strict):
    id: str
    at: datetime
    actor: Literal["closer", "user", "external", "system"]
    agent: str | None = None
    kind: str
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
    run_id: str | None = None


class OpenLoop(Strict):
    id: str
    title: str
    category: LoopCategory
    description: str
    source: str
    source_ref: str
    created_at: datetime
    deadline: datetime | None = None
    status: LoopStatus = LoopStatus.DISCOVERED
    priority: Priority = Priority.NORMAL
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    risk_level: RiskLevel = RiskLevel.READ_ONLY
    evidence: EvidenceBundle = Field(default_factory=EvidenceBundle)
    required_information: list[str] = Field(default_factory=list)
    available_actions: list[ActionType] = Field(default_factory=list)
    recommended_action: ProposedAction | None = None
    approval_required: bool = False
    assigned_agent: str = "supervisor"
    external_party: str | None = None
    last_activity: datetime | None = None
    next_check_at: datetime | None = None
    attempt_count: int = 0
    value_at_stake: float = 0.0
    currency: str = "INR"
    estimated_minutes_saved: int = 0
    resolution: str | None = None
    verification: VerificationResult | None = None
    audit_events: list[AuditEvent] = Field(default_factory=list)
    dedupe_key: str = ""
    notify: bool = False
    notify_reason: str = ""

    def add_audit(self, event: AuditEvent) -> None:
        self.audit_events.append(event)
        self.last_activity = event.at


# --------------------------------------------------------------------------
# Runs & telemetry
# --------------------------------------------------------------------------


class ToolInvocation(Strict):
    id: str
    run_id: str
    loop_id: str | None
    agent: str
    tool: str
    input_summary: str
    output_summary: str
    status: Literal["success", "error"]
    latency_ms: int
    at: datetime


class AgentRun(Strict):
    run_id: str
    session_id: str
    trigger: RunTrigger
    started_at: datetime
    finished_at: datetime | None = None
    status: RunStatus = RunStatus.RUNNING
    model_provider: str = "deterministic"
    events_scanned: int = 0
    events_ignored: int = 0
    loops_discovered: int = 0
    loops_advanced: int = 0
    loops_completed: int = 0
    loops_waiting: int = 0
    decisions_required: int = 0
    autonomous_actions: int = 0
    failures: int = 0
    retries: int = 0
    duplicates_suppressed: int = 0
    injection_attempts_blocked: int = 0
    tool_invocations: list[ToolInvocation] = Field(default_factory=list)
    minutes_saved: int = 0
    value_touched: float = 0.0
    notes: list[str] = Field(default_factory=list)


class ActivityStep(Strict):
    """One line in the 'What CLOSER is doing' view. Emitted by real execution,
    never by a fake animation."""

    at: datetime
    run_id: str
    label: str
    state: Literal["running", "done", "skipped", "failed"]
    loop_id: str | None = None
    agent: str | None = None
    detail: str = ""


# --------------------------------------------------------------------------
# Preferences
# --------------------------------------------------------------------------


class AutonomySettings(Strict):
    """User-configurable autonomy. Can only *tighten* or *loosen within* the
    hard-coded ceilings in the policy engine — never bypass them."""

    organize_documents: bool = True
    detect_duplicates: bool = True
    prepare_drafts: bool = True
    schedule_follow_ups: bool = True
    monitor_pending: bool = True
    auto_reschedule_appointments: bool = True
    external_messages: Literal["auto", "ask", "never"] = "ask"
    financial_actions: Literal["auto", "ask", "never"] = "ask"
    cancellations: Literal["auto", "ask", "never"] = "ask"
    sensitive_actions: Literal["auto", "ask", "never"] = "ask"
    payments: Literal["auto", "ask", "never"] = "never"
    irreversible_deletion: Literal["auto", "ask", "never"] = "never"
    notify_financial_above: float = 1000.0
    notify_deadline_within_days: int = 7
    quiet_routine_updates: bool = True
    focus_areas: list[str] = Field(
        default_factory=lambda: ["bills", "warranties", "appointments", "documents", "follow_ups"]
    )


class Notification(Strict):
    id: str
    at: datetime
    loop_id: str | None
    title: str
    body: str
    severity: Literal["decision", "deadline", "failure", "info"]
    read: bool = False


ActionPlan.model_rebuild()
