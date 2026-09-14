"""The whole thing, end to end:

    event → Strands agent → tool selection → evidence → decision → policy →
    action → verification → CLOSED

plus the specific behaviours the product depends on: knowing when not to act,
noticing stale evidence, refusing to act on missing facts, and surfacing exactly
one clear decision when judgement is genuinely required.
"""

from closer import orchestrator
from closer.models.enums import ActionStatus, ActionType, LoopCategory, LoopStatus, VerificationStatus
from closer.store.repository import Repo


def _loop(category: LoopCategory, contains: str = ""):
    return next(l for l in Repo.loops()
                if l.category is category and l.id.startswith("loop_")
                and (not contains or contains.lower() in l.title.lower()))


def test_a_full_pass_over_a_messy_week(seeded):
    run = orchestrator.run_once()

    assert run.status.value == "COMPLETED"
    assert run.events_scanned >= 15
    assert run.events_ignored >= 5, "CLOSER must recognise that most things need nothing"
    assert run.loops_discovered >= 6
    assert run.duplicates_suppressed >= 1
    assert run.injection_attempts_blocked >= 1
    assert run.failures == 0
    assert run.decisions_required >= 2
    assert run.autonomous_actions >= 3

    # Real agent execution leaves a real tool trail.
    tools_used = {t.tool for t in run.tool_invocations}
    assert {"get_inbox_item", "classify_item", "search_documents", "recommend_action",
            "submit_action_plan", "check_external_state"} <= tools_used
    agents_used = {t.agent for t in run.tool_invocations}
    assert {"intake", "supervisor", "evidence", "resolution", "verification"} <= agents_used


def test_warranty_loop_gathers_evidence_and_stops_for_approval(seeded):
    orchestrator.run_once()
    loop = _loop(LoopCategory.WARRANTY, "Sterling")

    assert loop.status is LoopStatus.HUMAN_DECISION
    assert loop.evidence.complete
    labels = {i.label for i in loop.evidence.items}
    assert {"Product", "Purchase date", "Serial number", "Warranty status"} <= labels
    assert loop.recommended_action.action_type is ActionType.SUBMIT_WARRANTY_CLAIM

    # Nothing has been sent.
    plan = Repo.plans_for_loop(loop.id)[-1]
    assert plan.status is ActionStatus.AWAITING_APPROVAL
    assert plan.executed_at is None

    # The decision card carries the whole investigation.
    card = Repo.approval(plan.plan_id)
    assert card is not None
    assert len(card.what_i_found) >= 5
    assert card.why_asking
    assert card.what_happens_if_approved


def test_approving_the_warranty_claim_executes_and_verifies(seeded):
    orchestrator.run_once()
    loop = _loop(LoopCategory.WARRANTY, "Sterling")
    plan_id = Repo.plans_for_loop(loop.id)[-1].plan_id

    result = orchestrator.apply_decision(plan_id, "approve")
    assert result["ok"] is True

    plan = Repo.plan(plan_id)
    assert plan.approved_by == "user"
    assert plan.status is ActionStatus.VERIFIED
    assert plan.execution_result["external_ref"]
    assert plan.verification.status is VerificationStatus.CONFIRMED
    # An external request leaves the loop open and monitored, not closed.
    assert Repo.loop(loop.id).status is LoopStatus.WAITING


def test_the_provider_replying_closes_the_loop(seeded):
    orchestrator.run_once()
    loop = _loop(LoopCategory.WARRANTY, "Sterling")
    plan_id = Repo.plans_for_loop(loop.id)[-1].plan_id
    orchestrator.apply_decision(plan_id, "approve")

    orchestrator.simulate_external_response(loop.id, "approved, engineer booked")
    closed = Repo.loop(loop.id)
    assert closed.status is LoopStatus.COMPLETED
    assert "approved" in (closed.resolution or "")
    assert closed.notify is False, "a closed loop should not interrupt anyone"


def test_rejecting_a_plan_cancels_the_loop_and_sends_nothing(seeded):
    orchestrator.run_once()
    loop = _loop(LoopCategory.WARRANTY, "Sterling")
    plan_id = Repo.plans_for_loop(loop.id)[-1].plan_id

    orchestrator.apply_decision(plan_id, "reject")
    assert Repo.plan(plan_id).status is ActionStatus.REJECTED
    assert Repo.plan(plan_id).executed_at is None
    assert Repo.loop(loop.id).status is LoopStatus.CANCELLED


def test_an_approval_cannot_be_replayed(seeded):
    orchestrator.run_once()
    loop = _loop(LoopCategory.WARRANTY, "Sterling")
    plan_id = Repo.plans_for_loop(loop.id)[-1].plan_id

    assert orchestrator.apply_decision(plan_id, "approve")["ok"] is True
    second = orchestrator.apply_decision(plan_id, "approve")
    assert second["ok"] is False
    assert "not awaiting approval" in second["error"]


def test_billing_duplicate_is_handled_without_interrupting_anyone(seeded):
    orchestrator.run_once()
    loop = next(l for l in Repo.loops() if "charged you twice" in l.title)

    plan = Repo.plans_for_loop(loop.id)[-1]
    assert plan.action.action_type is ActionType.SUBMIT_BILLING_DISPUTE
    assert plan.approved_by is None, "this one was inside the user's own limits"
    assert plan.policy.rule_id == "reversible.auto"
    assert plan.verification.status is VerificationStatus.CONFIRMED
    assert loop.status is LoopStatus.WAITING
    assert loop.notify is False


def test_closer_knows_when_not_to_act(seeded):
    """The fitness price rise is explained by a notice the provider sent in
    advance. The right answer is to close it quietly, not to complain."""
    orchestrator.run_once()
    loop = next(l for l in Repo.loops() if "Nimbus" in l.title)

    assert loop.status is LoopStatus.COMPLETED
    assert loop.recommended_action.action_type is ActionType.RECORD_FINDING
    assert "notified in advance" in loop.resolution
    assert loop.notify is False


def test_an_expired_warranty_produces_no_claim(seeded):
    """The laptop's warranty ran out fifteen months ago. The right answer is to
    say so and close the matter, not to file a claim that would be refused."""
    orchestrator.run_once()
    loop = _loop(LoopCategory.WARRANTY, "Vertex")

    status = next(i for i in loop.evidence.items if i.key == "warranty_status")
    assert status.value.startswith("Expired")
    assert loop.recommended_action.action_type is ActionType.RECORD_FINDING
    assert loop.status is LoopStatus.COMPLETED
    assert loop.notify is False
    assert loop.value_at_stake == 0.0
    assert not any(p.action.action_type is ActionType.SUBMIT_WARRANTY_CLAIM
                   for p in Repo.plans_for_loop(loop.id))


def test_stale_evidence_is_flagged_and_not_used(seeded):
    orchestrator.run_once()
    loop = _loop(LoopCategory.DOCUMENT_REQUEST)

    stale = [i for i in loop.evidence.items if i.stale]
    chosen = next(i for i in loop.evidence.items if i.key == "available_document")
    assert stale, "the superseded statement must be recorded as seen and rejected"
    assert "March" not in chosen.value
    assert loop.recommended_action.action_type is ActionType.UPLOAD_DOCUMENT


def test_an_ambiguous_appointment_becomes_a_choice_not_a_guess(seeded):
    orchestrator.run_once()
    loop = _loop(LoopCategory.APPOINTMENT)
    plan = Repo.plans_for_loop(loop.id)[-1]

    assert plan.policy.rule_id == "judgement.user_choice"
    card = Repo.approval(plan.plan_id)
    assert len(card.choices) == 2
    assert plan.executed_at is None


def test_choosing_a_slot_moves_the_calendar_and_verifies_it(seeded):
    orchestrator.run_once()
    loop = _loop(LoopCategory.APPOINTMENT)
    plan = Repo.plans_for_loop(loop.id)[-1]
    slots = [i.value for i in loop.evidence.items if i.key == "alternative_slots"]
    assert slots

    from closer.connectors.registry import get_connectors

    target = next(s for s in get_connectors().calendar.find_free_slots(45, 14, one_per_day=True)
                  if f"{s:%A %d %B, %H:%M}" == slots[0])

    result = orchestrator.apply_decision(plan.plan_id, "approve", choice=target.isoformat())
    assert result["ok"] is True

    event = Repo.calendar_event("cal_dental")
    assert event.starts_at == target
    assert event.confirmed is True
    assert Repo.plan(plan.plan_id).verification.status is VerificationStatus.CONFIRMED
    assert Repo.loop(loop.id).status is LoopStatus.COMPLETED


def test_a_decision_with_runway_is_not_notified(seeded):
    """Three decisions exist; only the two that actually matter now interrupt."""
    orchestrator.run_once()
    decisions = [l for l in Repo.loops() if l.status is LoopStatus.HUMAN_DECISION]
    assert len(decisions) >= 3
    held = [l for l in decisions if not l.notify]
    assert held, "at least one decision should be held until it is relevant"
    assert any("nearer the deadline" in l.notify_reason for l in held)


def test_every_evidence_item_resolves_to_a_real_source(seeded):
    orchestrator.run_once()
    for loop in Repo.loops():
        for item in loop.evidence.items:
            assert item.source_id
            assert item.source_title
            if item.source_type == "document":
                assert Repo.document(item.source_id) is not None
            elif item.source_type == "message":
                assert Repo.message(item.source_id) is not None


def test_drafted_messages_contain_only_sourced_facts(seeded, run_ctx):
    from closer.tools.communication import check_message_is_sourced, prepare_message

    orchestrator.run_once()
    loop = _loop(LoopCategory.WARRANTY, "Sterling")
    run_ctx.loop_id = loop.id
    prepare_message(loop_id=loop.id, action_type="SUBMIT_WARRANTY_CLAIM")
    check = check_message_is_sourced(loop_id=loop.id)
    assert check["facts"]["unsourced_claims"] == 0


def test_the_run_report_is_generated_from_real_execution(seeded):
    from closer import metrics

    run = orchestrator.run_once()
    report = metrics.run_report(run.run_id)

    assert report["counts"]["tool_calls"] == len(run.tool_invocations)
    assert report["counts"]["input_events"] == run.events_scanned
    assert len(report["activity"]) > 20
    assert report["data_notice"].startswith("All records are synthetic")


def test_the_demo_is_reproducible(seeded):
    """Two identical runs from a clean store produce the same outcome — the whole
    point of the deterministic demo path."""
    from closer.store import db

    first = orchestrator.run_once()
    snapshot = sorted((l.dedupe_key, l.status.value, l.recommended_action.action_type.value
                       if l.recommended_action else None) for l in Repo.loops())

    db.reset()
    from closer.demo.seed import seed as reseed

    reseed()
    second = orchestrator.run_once()
    assert sorted((l.dedupe_key, l.status.value, l.recommended_action.action_type.value
                   if l.recommended_action else None) for l in Repo.loops()) == snapshot
    assert (first.loops_discovered, first.decisions_required, first.autonomous_actions) == \
           (second.loops_discovered, second.decisions_required, second.autonomous_actions)
