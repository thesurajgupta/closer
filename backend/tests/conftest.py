from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Every test gets its own store, its own clock and its own id sequence, so
    a test can never depend on another test's leftovers."""
    monkeypatch.setenv("CLOSER_DB", str(tmp_path / "closer.db"))
    monkeypatch.setenv("CLOSER_MODE", "demo")
    monkeypatch.setenv("CLOSER_DEMO_NOW", "2026-09-10T09:00:00")
    monkeypatch.setenv("CLOSER_MODEL_PROVIDER", "deterministic")
    monkeypatch.delenv("CLOSER_DEMO_FAIL_ONCE", raising=False)

    from closer import clock, config, ids
    from closer.connectors import demo as demo_connectors
    from closer.connectors import registry
    from closer.store import db

    config.reset_settings()
    registry.reset_connectors()
    clock.reset()
    ids.reset_sequences()
    demo_connectors.FAIL_ONCE = []
    # Keep the structured log out of test output; the tests assert on state.
    import logging

    logging.getLogger("closer").setLevel(logging.WARNING)

    db.close()
    yield
    db.close()
    config.reset_settings()
    registry.reset_connectors()


@pytest.fixture
def seeded():
    from closer.demo.seed import seed

    return seed()


@pytest.fixture
def run_ctx():
    """A live run context, for tests that drive tools directly."""
    from closer import clock
    from closer.agents import context as run_context
    from closer.agents.context import RunContext
    from closer.models.domain import AgentRun
    from closer.models.enums import RunTrigger

    run = AgentRun(run_id="run_test", session_id="sess_test", trigger=RunTrigger.MANUAL,
                   started_at=clock.now())
    ctx = RunContext(run=run)
    token = run_context.set_context(ctx)
    try:
        yield ctx
    finally:
        run_context.reset_context(token)


@pytest.fixture
def api_client():
    from fastapi.testclient import TestClient

    from closer.api.app import app

    with TestClient(app) as client:
        yield client
