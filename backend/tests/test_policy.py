"""The policy engine is the only thing standing between a model's suggestion and
a real side effect, so it gets the most tests."""

import pytest

from closer.models.domain import AutonomySettings, EvidenceBundle, EvidenceItem, OpenLoop, ProposedAction
from closer.models.enums import ActionType, LoopCategory, PolicyDecision, RiskLevel
from closer.policy import engine


def _loop(**kw) -> OpenLoop:
    from closer import clock

    base = dict(
        id="loop_x", title="t", category=LoopCategory.WARRANTY, description="d",
        source="email", source_ref="msg_x", created_at=clock.now(),
    )
    base.update(kw)
    return OpenLoop(**base)


def _ctx(loop=None, settings=None, complete=True, confidence=0.9, trusted=True):
    return engine.PolicyContext(
        loop=loop or _loop(), settings=settings or AutonomySettings(),
        evidence_complete=complete, confidence=confidence, external_content_trusted=trusted,
    )


def _action(action_type=ActionType.SEND_MESSAGE, **kw) -> ProposedAction:
    base = dict(action_type=action_type, summary="s", rationale="r", target="them",
                evidence_ids=["ev_1"])
    base.update(kw)
    return ProposedAction(**base)


def test_read_only_is_autonomous():
    result = engine.evaluate(_action(ActionType.RECORD_FINDING), _ctx())
    assert result.decision is PolicyDecision.ALLOW_AUTONOMOUS


def test_external_communication_requires_approval_by_default():
    result = engine.evaluate(_action(ActionType.SEND_MESSAGE), _ctx())
    assert result.decision is PolicyDecision.REQUIRE_APPROVAL
    assert result.rule_id == "external_communication.ask"


def test_user_can_widen_external_messages_but_not_payments():
    settings = AutonomySettings(external_messages="auto")
    assert engine.evaluate(_action(ActionType.SEND_MESSAGE), _ctx(settings=settings)).decision \
        is PolicyDecision.ALLOW_AUTONOMOUS

    settings = AutonomySettings(payments="auto")  # the model of a careless user
    result = engine.evaluate(_action(ActionType.ISSUE_PAYMENT, amount=100), _ctx(settings=settings))
    assert result.decision is PolicyDecision.DENY
    assert result.rule_id == "ceiling.no_payments_in_demo"


def test_hard_value_ceiling_cannot_be_raised_by_settings():
    settings = AutonomySettings(financial_actions="auto", notify_financial_above=10_000_000)
    result = engine.evaluate(_action(ActionType.CANCEL_SERVICE, amount=999_999), _ctx(settings=settings))
    assert result.decision is PolicyDecision.DENY
    assert result.rule_id == "ceiling.amount"


def test_untrusted_source_blocks_anything_consequential():
    result = engine.evaluate(_action(ActionType.SEND_MESSAGE), _ctx(trusted=False))
    assert result.decision is PolicyDecision.DENY
    assert result.rule_id == "ceiling.untrusted_source"


def test_untrusted_source_still_allows_reading_and_filing():
    result = engine.evaluate(_action(ActionType.RECORD_FINDING), _ctx(trusted=False))
    assert result.decision is PolicyDecision.ALLOW_AUTONOMOUS


def test_incomplete_evidence_escalates_rather_than_guessing():
    result = engine.evaluate(_action(ActionType.SEND_MESSAGE), _ctx(complete=False))
    assert result.decision is PolicyDecision.REQUIRE_APPROVAL
    assert result.rule_id == "evidence.incomplete"


def test_an_unsourced_external_action_is_refused_outright():
    result = engine.evaluate(_action(ActionType.SEND_MESSAGE, evidence_ids=[]), _ctx())
    assert result.decision is PolicyDecision.DENY
    assert result.rule_id == "evidence.unsourced"


def test_low_confidence_escalates():
    result = engine.evaluate(_action(ActionType.SCHEDULE_FOLLOW_UP), _ctx(confidence=0.4))
    assert result.decision is PolicyDecision.REQUIRE_APPROVAL
    assert result.rule_id == "confidence.low"


def test_a_genuine_choice_always_goes_to_the_human():
    action = _action(ActionType.RESCHEDULE_APPOINTMENT, payload={"options": ["Tue 11:00", "Wed 14:00"]})
    result = engine.evaluate(action, _ctx())
    assert result.decision is PolicyDecision.REQUIRE_APPROVAL
    assert result.rule_id == "judgement.user_choice"


def test_a_chosen_option_no_longer_needs_a_choice():
    action = _action(ActionType.RESCHEDULE_APPOINTMENT,
                     payload={"options": ["Tue 11:00", "Wed 14:00"], "chosen_slot": "2026-09-16T16:30:00"})
    assert engine.evaluate(action, _ctx()).decision is PolicyDecision.ALLOW_AUTONOMOUS


def test_financial_threshold_governs_reversible_money():
    settings = AutonomySettings(notify_financial_above=1000)
    below = engine.evaluate(_action(ActionType.SUBMIT_BILLING_DISPUTE, amount=999), _ctx(settings=settings))
    above = engine.evaluate(_action(ActionType.SUBMIT_BILLING_DISPUTE, amount=1001), _ctx(settings=settings))
    assert below.decision is PolicyDecision.ALLOW_AUTONOMOUS
    assert above.decision is PolicyDecision.REQUIRE_APPROVAL
    assert above.rule_id == "threshold.financial_review"


def test_never_preference_denies():
    settings = AutonomySettings(sensitive_actions="never")
    result = engine.evaluate(_action(ActionType.UPLOAD_DOCUMENT), _ctx(settings=settings))
    assert result.decision is PolicyDecision.DENY
    assert result.rule_id == "preference.never"


def test_irreversible_actions_always_ask():
    settings = AutonomySettings(external_messages="auto")
    result = engine.evaluate(_action(ActionType.SEND_MESSAGE, reversible=False), _ctx(settings=settings))
    assert result.decision is PolicyDecision.REQUIRE_APPROVAL
    assert result.rule_id == "reversibility.irreversible"


def test_every_allowlisted_action_has_a_risk_class():
    for action_type in ActionType:
        assert action_type in engine.ACTION_RISK
        assert isinstance(engine.ACTION_RISK[action_type], RiskLevel)


def test_cancellation_is_never_autonomous_whatever_the_settings():
    settings = AutonomySettings(cancellations="auto", financial_actions="auto")
    result = engine.evaluate(_action(ActionType.CANCEL_SERVICE, amount=10), _ctx(settings=settings))
    assert result.decision is PolicyDecision.REQUIRE_APPROVAL


def test_explain_settings_covers_every_control():
    summary = engine.explain_settings(AutonomySettings())
    flat = summary["automatically"] + summary["ask_first"] + summary["never"]
    assert "Payments" in summary["never"]
    assert "Send external messages" in summary["ask_first"]
    assert len(flat) == len(set(flat))
