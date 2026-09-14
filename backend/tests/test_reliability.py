"""Idempotency, retries, crash recovery and verification.

These are the tests that decide whether it is safe to let CLOSER act without a
person watching.
"""

from datetime import timedelta

import pytest

from closer import clock, orchestrator
from closer.models.enums import ActionStatus, LoopStatus, VerificationStatus
from closer.store import db
from closer.store.repository import Repo


def _plan_for(action_type: str):
    for plan in Repo.plans():
        if plan.action.action_type.value == action_type:
            return plan
    raise AssertionError(f"no plan of type {action_type}")


def test_duplicate_inbound_items_do_not_open_two_loops(seeded):
    run = orchestrator.run_once()
    # The dataset contains a resent invoice mail with the same invoice reference.
    assert run.duplicates_suppressed >= 1
    keys = [loop.dedupe_key for loop in Repo.loops()]
    assert len(keys) == len(set(keys))


def test_a_second_run_creates_no_duplicate_work(seeded):
    first = orchestrator.run_once()
    loops_after_first = {l.id for l in Repo.loops()}
    plans_after_first = {p.plan_id for p in Repo.plans()}

    second = orchestrator.run_once()
    assert {l.id for l in Repo.loops()} == loops_after_first
    # No new side effects: any plan added is an idempotent no-op re-derivation.
    new_plans = {p.plan_id for p in Repo.plans()} - plans_after_first
    for plan_id in new_plans:
        assert Repo.plan(plan_id).status is not ActionStatus.EXECUTED
    assert second.events_scanned == 0  # everything was already processed


def test_idempotency_key_is_stable_across_runs(seeded):
    orchestrator.run_once()
    plan = _plan_for("SUBMIT_BILLING_DISPUTE")
    key = plan.idempotency_key
    orchestrator.run_once()
    assert _plan_for("SUBMIT_BILLING_DISPUTE").idempotency_key == key


def test_a_crash_after_the_side_effect_does_not_repeat_it(seeded, run_ctx):
    """Simulates the worst case: the connector succeeded, the process died before
    the result was written. On restart the claim is already there."""
    from closer.execution.executor import execute

    orchestrator.run_once()
    plan = _plan_for("SUBMIT_BILLING_DISPUTE")
    original_ref = plan.execution_result["external_ref"]

    # Rewind the plan as though the crash happened, leaving the claim in place.
    plan.status = ActionStatus.APPROVED
    plan.execution_result = None
    plan.executed_at = None
    Repo.put_plan(plan)
    loop = Repo.loop(plan.loop_id)
    loop.status = LoopStatus.READY
    Repo.put_loop(loop)

    replayed = execute(plan)
    assert replayed.status is ActionStatus.EXECUTED
    assert replayed.execution_result["external_ref"] == original_ref
    # Exactly one dispute exists on the provider side.
    disputes = [k for k in db.all_rows("external_state")]
    assert sum(1 for d in disputes if d.get("provider")) == 1


def test_a_retryable_connector_failure_is_retried_and_succeeds(seeded, monkeypatch):
    from closer.connectors import demo as demo_connectors

    monkeypatch.setattr(demo_connectors, "FAIL_ONCE", ["billing.dispute"])
    run = orchestrator.run_once()
    plan = _plan_for("SUBMIT_BILLING_DISPUTE")
    assert plan.status in (ActionStatus.EXECUTED, ActionStatus.VERIFIED)
    assert plan.attempt_count == 2
    assert run.retries >= 1


def test_a_permanently_failing_connector_fails_loudly_and_schedules_a_retry(seeded, monkeypatch):
    from closer.connectors.base import ConnectorError
    from closer.connectors.registry import get_connectors

    def always_fail(*_args, **_kwargs):
        raise ConnectorError("provider unreachable", retryable=True)

    monkeypatch.setattr(get_connectors().billing, "dispute", always_fail)
    orchestrator.run_once()
    plan = _plan_for("SUBMIT_BILLING_DISPUTE")
    assert plan.status is ActionStatus.FAILED
    assert plan.attempt_count == 3
    loop = Repo.loop(plan.loop_id)
    assert loop.status is LoopStatus.FAILED
    assert loop.next_check_at is not None
    assert loop.notify is True  # a failure is always worth interrupting for


def test_a_failed_loop_can_be_retried_by_hand(seeded, monkeypatch):
    from closer.connectors.base import ConnectorError
    from closer.connectors.registry import get_connectors

    calls = {"n": 0}
    real = get_connectors().billing.dispute

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] <= 3:
            raise ConnectorError("provider unreachable", retryable=True)
        return real(*args, **kwargs)

    monkeypatch.setattr(get_connectors().billing, "dispute", flaky)
    orchestrator.run_once()
    plan = _plan_for("SUBMIT_BILLING_DISPUTE")
    assert plan.status is ActionStatus.FAILED

    result = orchestrator.retry_failed(plan.loop_id)
    assert result["ok"] is True
    assert Repo.plan(plan.plan_id).status in (ActionStatus.EXECUTED, ActionStatus.VERIFIED)


def test_verification_confirms_against_provider_state(seeded):
    orchestrator.run_once()
    plan = _plan_for("SUBMIT_BILLING_DISPUTE")
    assert plan.verification is not None
    assert plan.verification.status is VerificationStatus.CONFIRMED
    assert all(check["pass"] for check in plan.verification.checks)


def test_verification_failure_keeps_the_loop_open(seeded, monkeypatch):
    from closer.connectors.registry import get_connectors

    orchestrator.run_once()
    plan = _plan_for("SUBMIT_BILLING_DISPUTE")

    # The provider forgot about it. CLOSER must not claim success.
    monkeypatch.setattr(get_connectors().billing, "dispute_state", lambda ref: {"status": "unknown"})
    from closer.agents.context import RunContext
    from closer.agents import context as run_context
    from closer.models.domain import AgentRun
    from closer.models.enums import RunTrigger

    run = AgentRun(run_id="run_v", session_id="s", trigger=RunTrigger.MANUAL, started_at=clock.now())
    ctx = RunContext(run=run)
    token = run_context.set_context(ctx)
    try:
        loop = Repo.loop(plan.loop_id)
        loop.status = LoopStatus.VERIFYING
        Repo.put_loop(loop)
        plan.status = ActionStatus.EXECUTED
        Repo.put_plan(plan)
        orchestrator.verify(ctx, plan.plan_id)
    finally:
        run_context.reset_context(token)

    refreshed = Repo.plan(plan.plan_id)
    assert refreshed.verification.status is VerificationStatus.FAILED
    assert Repo.loop(plan.loop_id).status is LoopStatus.FAILED


def test_expired_deadlines_are_swept_without_interrupting_anyone(seeded):
    from closer.background.worker import deadline_sweep

    orchestrator.run_once()
    loop = next(l for l in Repo.active_loops() if l.deadline)
    loop.deadline = clock.now() - timedelta(days=5)
    loop.status = LoopStatus.READY
    Repo.put_loop(loop)

    result = deadline_sweep()
    assert loop.id in result["expired"]
    assert Repo.loop(loop.id).status is LoopStatus.EXPIRED


def test_deadline_pressure_raises_priority(seeded):
    from closer.background.worker import deadline_sweep
    from closer.models.enums import Priority

    orchestrator.run_once()
    loop = next(l for l in Repo.active_loops() if l.status is LoopStatus.WAITING)
    loop.deadline = clock.now() + timedelta(days=2)
    loop.priority = Priority.NORMAL
    Repo.put_loop(loop)

    deadline_sweep()
    assert Repo.loop(loop.id).priority is Priority.URGENT


def test_the_background_queue_dead_letters_after_repeated_failure(seeded):
    from closer.background.scheduler import EventQueue

    queue = EventQueue(max_receives=2)
    queue.send({"detail-type": "closer.discovery"})

    def broken(_event):
        raise RuntimeError("handler exploded")

    for _ in range(3):
        queue.drain(broken)
    assert len(queue.dead_letter) == 1
    assert "handler exploded" in queue.dead_letter[0]["error"]


def test_an_unknown_background_event_is_rejected(seeded):
    from closer.background.worker import handle_event

    with pytest.raises(ValueError):
        handle_event({"detail-type": "closer.do_something_weird"})
