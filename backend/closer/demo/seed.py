"""Seed the local store with the synthetic household and one already-closed
historical loop (so the impact dashboard has honest prior-week context)."""

from __future__ import annotations

from datetime import timedelta

from .. import clock
from ..models.domain import (
    ActionPlan,
    AuditEvent,
    AutonomySettings,
    EvidenceBundle,
    EvidenceItem,
    OpenLoop,
    PolicyEvaluation,
    ProposedAction,
    VerificationResult,
)
from ..models.enums import (
    ActionStatus,
    ActionType,
    LoopCategory,
    LoopStatus,
    PolicyDecision,
    Priority,
    RiskLevel,
    VerificationStatus,
)
from ..ids import stable_id
from ..store import db
from ..store.repository import Repo
from . import dataset


def seed(reset: bool = True) -> dict[str, int]:
    if reset:
        db.reset()
    for doc in dataset.documents():
        Repo.put_document(doc)
    for msg in dataset.messages():
        Repo.put_message(msg)
    for ev in dataset.calendar():
        Repo.put_calendar(ev)
    for rec in dataset.billing():
        Repo.put_billing(rec)
    for war in dataset.warranties():
        Repo.put_warranty(war)
    Repo.put_settings(AutonomySettings())
    _seed_history()
    return dataset.counts()


def _seed_history() -> None:
    """Two loops CLOSER handled earlier in the week: one closed, one still
    waiting on a provider. They give the dashboard a truthful baseline instead
    of invented numbers."""
    now = clock.now()

    closed = OpenLoop(
        id="loop_skyline_refund",
        title="Skyline Travel refund for cancelled booking",
        category=LoopCategory.REFUND,
        description="Booking SKY-71829 was cancelled by the airline; the refund had not been credited.",
        source="email", source_ref="msg_skyline_closed",
        created_at=now - timedelta(days=19),
        status=LoopStatus.COMPLETED,
        priority=Priority.NORMAL, confidence=0.96, risk_level=RiskLevel.EXTERNAL_COMMUNICATION,
        evidence=EvidenceBundle(complete=True, items=[
            EvidenceItem(id="ev_hist_1", label="Refund settled", value="INR 4,310.00 credited, ref RF-31882",
                         source_type="document", source_id="doc_refund_closed",
                         source_title="Skyline Travel — refund settlement confirmation", locator="body"),
        ]),
        external_party="Skyline Travel",
        value_at_stake=4310.0, estimated_minutes_saved=35,
        resolution="Refund of INR 4,310.00 credited to the original payment method (ref RF-31882).",
        verification=VerificationResult(status=VerificationStatus.CONFIRMED,
                                        detail="Settlement letter received and matched to the claim.",
                                        verified_at=now - timedelta(days=11),
                                        checks=[{"check": "settlement_letter", "result": "found"}]),
        last_activity=now - timedelta(days=11),
        dedupe_key="refund:skyline:SKY-71829",
        audit_events=[
            AuditEvent(id="aud_h1", at=now - timedelta(days=19), actor="closer", agent="intake",
                       kind="discovered", message="Found an unpaid refund for a cancelled booking."),
            AuditEvent(id="aud_h2", at=now - timedelta(days=19), actor="closer", agent="communication",
                       kind="drafted", message="Prepared a refund follow-up citing the airline's cancellation."),
            AuditEvent(id="aud_h3", at=now - timedelta(days=18), actor="user",
                       kind="approved", message="You approved sending the follow-up."),
            AuditEvent(id="aud_h4", at=now - timedelta(days=18), actor="closer", agent="executor",
                       kind="executed", message="Follow-up sent to Skyline Travel."),
            AuditEvent(id="aud_h5", at=now - timedelta(days=11), actor="external",
                       kind="response", message="Skyline Travel confirmed settlement of INR 4,310.00."),
            AuditEvent(id="aud_h6", at=now - timedelta(days=11), actor="closer", agent="verification",
                       kind="verified", message="Verified the credit reference and closed the loop."),
        ],
    )
    Repo.put_loop(closed)

    waiting = OpenLoop(
        id="loop_aurora_chimney",
        title="Aurora Appliances installation visit — ticket AA-77120",
        category=LoopCategory.OTHER,
        description="A scheduling agent was promised within 3 working days and has not made contact.",
        source="email", source_ref="msg_aurora_ticket_ack",
        created_at=now - timedelta(days=9),
        status=LoopStatus.WAITING,
        priority=Priority.NORMAL, confidence=0.9, risk_level=RiskLevel.EXTERNAL_COMMUNICATION,
        evidence=EvidenceBundle(complete=True, items=[
            EvidenceItem(id="ev_hist_2", label="Ticket acknowledged", value="AA-77120, 3 working day SLA",
                         source_type="message", source_id="msg_aurora_ticket_ack",
                         source_title="Ticket AA-77120 received", locator="body"),
        ]),
        external_party="Aurora Appliances",
        estimated_minutes_saved=15,
        next_check_at=now + timedelta(days=1),
        attempt_count=1,
        last_activity=now - timedelta(days=9),
        dedupe_key="followup:aurora-appliances:AA-77120",
        audit_events=[
            AuditEvent(id="aud_h7", at=now - timedelta(days=9), actor="closer", agent="intake",
                       kind="discovered", message="Logged ticket AA-77120 and started tracking the 3-day SLA."),
            AuditEvent(id="aud_h8", at=now - timedelta(days=9), actor="closer", agent="supervisor",
                       kind="scheduled", message="Follow-up scheduled for the day the SLA lapses."),
        ],
    )
    Repo.put_loop(waiting)

    # A historical plan so the audit trail has a real approval record in it.
    plan = ActionPlan(
        plan_id="plan_hist_skyline",
        loop_id="loop_skyline_refund",
        run_id="run_history",
        action=ProposedAction(
            action_type=ActionType.SEND_MESSAGE,
            summary="Send a refund follow-up to Skyline Travel citing the airline cancellation.",
            rationale="The airline cancelled the booking and 14 days had passed without a credit.",
            target="care@skyline-travel.test",
            amount=4310.0,
            evidence_ids=["ev_hist_1"],
        ),
        risk_level=RiskLevel.EXTERNAL_COMMUNICATION,
        policy=PolicyEvaluation(decision=PolicyDecision.REQUIRE_APPROVAL, risk_level=RiskLevel.EXTERNAL_COMMUNICATION,
                                rule_id="external_communication.ask", requires_approval=True,
                                reasons=["Sends a message to an external party on your behalf."]),
        status=ActionStatus.VERIFIED,
        idempotency_key=stable_id("idem", "loop_skyline_refund", "SEND_MESSAGE", "skyline"),
        created_at=now - timedelta(days=19),
        approved_at=now - timedelta(days=18), approved_by="user",
        executed_at=now - timedelta(days=18),
        execution_result={"external_ref": "out_hist_skyline", "detail": "Follow-up sent."},
        verification=VerificationResult(status=VerificationStatus.CONFIRMED,
                                        detail="Provider confirmed settlement.",
                                        verified_at=now - timedelta(days=11)),
    )
    Repo.put_plan(plan)
