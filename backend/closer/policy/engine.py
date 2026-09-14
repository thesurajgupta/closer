"""The policy engine.

This is the only component in CLOSER that grants execution authority. A model
can *propose* anything; nothing runs unless `evaluate()` returns
ALLOW_AUTONOMOUS, or a human approves the exact plan the engine gated.

Three layers, evaluated in order, each able only to *narrow* the previous one:

  1. HARD CEILING   — compiled into code. No preference and no prompt can widen
                      it. Payments and irreversible deletion live here.
  2. RISK DEFAULTS  — the risk class of the action type.
  3. USER AUTONOMY  — the user's own settings and thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import clock
from ..config import get_settings
from ..models.domain import AutonomySettings, OpenLoop, PolicyEvaluation, ProposedAction
from ..models.enums import ActionType, PolicyDecision, RiskLevel

# --- layer 1: the allowlist and its immovable ceiling -----------------------

ACTION_RISK: dict[ActionType, RiskLevel] = {
    ActionType.NO_ACTION: RiskLevel.READ_ONLY,
    ActionType.RECORD_FINDING: RiskLevel.READ_ONLY,
    ActionType.ORGANIZE_DOCUMENT: RiskLevel.LOW_RISK,
    ActionType.PREPARE_DRAFT: RiskLevel.LOW_RISK,
    ActionType.SCHEDULE_FOLLOW_UP: RiskLevel.REVERSIBLE,
    ActionType.RESCHEDULE_APPOINTMENT: RiskLevel.REVERSIBLE,
    ActionType.CONFIRM_APPOINTMENT: RiskLevel.REVERSIBLE,
    ActionType.SUBMIT_BILLING_DISPUTE: RiskLevel.REVERSIBLE,
    ActionType.SEND_MESSAGE: RiskLevel.EXTERNAL_COMMUNICATION,
    ActionType.SUBMIT_WARRANTY_CLAIM: RiskLevel.EXTERNAL_COMMUNICATION,
    ActionType.UPLOAD_DOCUMENT: RiskLevel.SENSITIVE,
    ActionType.CANCEL_SERVICE: RiskLevel.FINANCIAL,
    ActionType.ISSUE_PAYMENT: RiskLevel.FINANCIAL,
}

# Never autonomous under any configuration.
NEVER_AUTONOMOUS: set[ActionType] = {ActionType.ISSUE_PAYMENT, ActionType.CANCEL_SERVICE}

# Never executed at all in demo mode, even with approval — CLOSER will not move
# money inside a synthetic environment and then imply that it happened.
DENY_IN_DEMO: set[ActionType] = {ActionType.ISSUE_PAYMENT}


@dataclass
class PolicyContext:
    loop: OpenLoop
    settings: AutonomySettings
    evidence_complete: bool
    confidence: float
    external_content_trusted: bool = True


def risk_for(action_type: ActionType) -> RiskLevel:
    return ACTION_RISK.get(action_type, RiskLevel.SENSITIVE)


def _pref_for(risk: RiskLevel, action_type: ActionType, s: AutonomySettings) -> str:
    if action_type is ActionType.ISSUE_PAYMENT:
        return s.payments
    if risk is RiskLevel.EXTERNAL_COMMUNICATION:
        return s.external_messages
    if risk is RiskLevel.FINANCIAL:
        return s.cancellations if action_type is ActionType.CANCEL_SERVICE else s.financial_actions
    if risk is RiskLevel.SENSITIVE:
        return s.sensitive_actions
    if risk is RiskLevel.REVERSIBLE:
        if action_type is ActionType.SCHEDULE_FOLLOW_UP and not s.schedule_follow_ups:
            return "ask"
        if action_type in (ActionType.RESCHEDULE_APPOINTMENT, ActionType.CONFIRM_APPOINTMENT) \
                and not s.auto_reschedule_appointments:
            return "ask"
        return "auto"
    if risk is RiskLevel.LOW_RISK:
        if action_type is ActionType.PREPARE_DRAFT and not s.prepare_drafts:
            return "ask"
        if action_type is ActionType.ORGANIZE_DOCUMENT and not s.organize_documents:
            return "ask"
        return "auto"
    return "auto"


def evaluate(action: ProposedAction, ctx: PolicyContext) -> PolicyEvaluation:
    settings = get_settings()
    reasons: list[str] = []
    risk = risk_for(action.action_type)

    # -- layer 0: allowlist -------------------------------------------------
    if action.action_type not in ACTION_RISK:
        return PolicyEvaluation(
            decision=PolicyDecision.DENY, risk_level=RiskLevel.SENSITIVE, rule_id="allowlist.unknown_action",
            requires_approval=False, reasons=[f"'{action.action_type}' is not an action CLOSER can perform."],
        )

    # -- layer 1: hard ceiling ---------------------------------------------
    if action.action_type in DENY_IN_DEMO and settings.is_demo:
        return PolicyEvaluation(
            decision=PolicyDecision.DENY, risk_level=risk, rule_id="ceiling.no_payments_in_demo",
            requires_approval=False,
            reasons=["CLOSER never moves money. In demo mode a payment is refused outright rather than "
                     "simulated, so nothing can be mistaken for a real transfer."],
        )
    if action.amount > settings.max_action_amount:
        return PolicyEvaluation(
            decision=PolicyDecision.DENY, risk_level=risk, rule_id="ceiling.amount",
            requires_approval=False,
            reasons=[f"Value {action.amount:,.0f} exceeds the hard ceiling of "
                     f"{settings.max_action_amount:,.0f} that no setting can raise."],
        )
    if not ctx.external_content_trusted and risk not in (RiskLevel.READ_ONLY, RiskLevel.LOW_RISK):
        return PolicyEvaluation(
            decision=PolicyDecision.DENY, risk_level=risk, rule_id="ceiling.untrusted_source",
            requires_approval=False,
            reasons=["The only source supporting this action contains instruction-shaped content and is "
                     "not trusted. CLOSER will not act on it."],
        )

    # -- layer 2: evidence & confidence gates ------------------------------
    if risk in (RiskLevel.EXTERNAL_COMMUNICATION, RiskLevel.FINANCIAL, RiskLevel.SENSITIVE):
        if not ctx.evidence_complete:
            reasons.append("Supporting evidence is incomplete.")
            return PolicyEvaluation(
                decision=PolicyDecision.REQUIRE_APPROVAL, risk_level=risk, rule_id="evidence.incomplete",
                requires_approval=True, reasons=reasons,
                approval_prompt="I could not gather every required fact, so I'd rather you check this.",
            )
        if not action.evidence_ids:
            return PolicyEvaluation(
                decision=PolicyDecision.DENY, risk_level=risk, rule_id="evidence.unsourced",
                requires_approval=False,
                reasons=["The proposed action cites no evidence. Unsourced claims are never sent externally."],
            )
    # A genuine choice between defensible options is not CLOSER's to make.
    options = action.payload.get("options") or []
    if len(options) > 1 and not action.payload.get("chosen_slot"):
        return PolicyEvaluation(
            decision=PolicyDecision.REQUIRE_APPROVAL, risk_level=risk, rule_id="judgement.user_choice",
            requires_approval=True,
            reasons=[f"There are {len(options)} reasonable options and picking between them is a "
                     "preference, not a fact."],
            approval_prompt="Both of these work — which one suits you?",
        )

    if ctx.confidence < 0.6 and risk is not RiskLevel.READ_ONLY:
        return PolicyEvaluation(
            decision=PolicyDecision.REQUIRE_APPROVAL, risk_level=risk, rule_id="confidence.low",
            requires_approval=True,
            reasons=[f"Confidence in this reading of the situation is {ctx.confidence:.0%}."],
            approval_prompt="I'm not confident enough in my reading of this to act on my own.",
        )

    # -- layer 3: user autonomy settings ------------------------------------
    if action.action_type in NEVER_AUTONOMOUS:
        reasons.append("This action class always requires you, regardless of settings.")
        pref = "ask"
    else:
        pref = _pref_for(risk, action.action_type, ctx.settings)

    if pref == "never":
        return PolicyEvaluation(
            decision=PolicyDecision.DENY, risk_level=risk, rule_id="preference.never",
            requires_approval=False,
            reasons=[f"Your settings say CLOSER must never do this ({risk.value.lower().replace('_',' ')})."],
        )

    # Financial threshold: below the user's review threshold, reversible money
    # matters can proceed; above it, they always come back to the user.
    if risk is RiskLevel.REVERSIBLE and action.amount > 0:
        if action.amount > ctx.settings.notify_financial_above:
            return PolicyEvaluation(
                decision=PolicyDecision.REQUIRE_APPROVAL, risk_level=risk, rule_id="threshold.financial_review",
                requires_approval=True,
                reasons=[f"INR {action.amount:,.0f} is above your INR "
                         f"{ctx.settings.notify_financial_above:,.0f} review threshold."],
                approval_prompt="This is above the amount you asked to review yourself.",
            )
        reasons.append(f"INR {action.amount:,.0f} is below your INR "
                       f"{ctx.settings.notify_financial_above:,.0f} review threshold.")

    if pref == "ask":
        reasons.append(_ask_reason(risk, action.action_type))
        return PolicyEvaluation(
            decision=PolicyDecision.REQUIRE_APPROVAL, risk_level=risk,
            rule_id=f"{risk.value.lower()}.ask", requires_approval=True, reasons=reasons,
            approval_prompt=_ask_reason(risk, action.action_type),
        )

    if not action.reversible and risk is not RiskLevel.READ_ONLY:
        return PolicyEvaluation(
            decision=PolicyDecision.REQUIRE_APPROVAL, risk_level=risk, rule_id="reversibility.irreversible",
            requires_approval=True, reasons=["This action cannot be undone."],
            approval_prompt="This one can't be undone, so I'd like your go-ahead.",
        )

    reasons.append(f"{risk.value.replace('_', ' ').lower()} action permitted by your autonomy settings.")
    return PolicyEvaluation(
        decision=PolicyDecision.ALLOW_AUTONOMOUS, risk_level=risk,
        rule_id=f"{risk.value.lower()}.auto", requires_approval=False, reasons=reasons,
    )


def _ask_reason(risk: RiskLevel, action_type: ActionType) -> str:
    return {
        RiskLevel.EXTERNAL_COMMUNICATION: "This creates an external request on your behalf.",
        RiskLevel.FINANCIAL: "This changes something financial.",
        RiskLevel.SENSITIVE: "This shares a personal document with a third party.",
        RiskLevel.REVERSIBLE: "Your settings ask CLOSER to check with you first for this.",
        RiskLevel.LOW_RISK: "Your settings ask CLOSER to check with you first for this.",
        RiskLevel.READ_ONLY: "Your settings ask CLOSER to check with you first for this.",
    }[risk] if action_type is not ActionType.ISSUE_PAYMENT else "CLOSER never moves money on its own."


def explain_settings(s: AutonomySettings) -> dict[str, list[str]]:
    """Human-readable rendering of the current policy, for the settings screen."""
    auto, ask, never = [], [], []
    labels = {
        "organize_documents": "Organise and file documents",
        "detect_duplicates": "Detect duplicate charges and requests",
        "prepare_drafts": "Prepare drafts and claims",
        "schedule_follow_ups": "Schedule follow-ups",
        "monitor_pending": "Monitor pending requests",
        "auto_reschedule_appointments": "Reschedule and confirm appointments",
    }
    for key, label in labels.items():
        (auto if getattr(s, key) else ask).append(label)
    for key, label in (("external_messages", "Send external messages"),
                       ("financial_actions", "Financial changes"),
                       ("cancellations", "Cancellations"),
                       ("sensitive_actions", "Share sensitive documents"),
                       ("payments", "Payments"),
                       ("irreversible_deletion", "Irreversible deletion")):
        value = getattr(s, key)
        {"auto": auto, "ask": ask, "never": never}[value].append(label)
    return {"automatically": auto, "ask_first": ask, "never": never}


def next_check_delay_days(risk: RiskLevel) -> float:
    return {RiskLevel.READ_ONLY: 7.0, RiskLevel.LOW_RISK: 3.0, RiskLevel.REVERSIBLE: 3.0,
            RiskLevel.EXTERNAL_COMMUNICATION: 3.0, RiskLevel.FINANCIAL: 2.0, RiskLevel.SENSITIVE: 2.0}[risk]


def deadline_pressure(loop: OpenLoop) -> float:
    days = clock.days_until(loop.deadline)
    if days is None:
        return 0.0
    if days <= 0:
        return 1.0
    return max(0.0, min(1.0, 1.0 - days / 30.0))
