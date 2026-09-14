"""The loop state machine is the thing that stops a model from declaring
victory. These tests pin its shape."""

import pytest

from closer.models.enums import LoopStatus as S
from closer.state import machine


def test_every_state_has_a_transition_rule():
    assert set(machine.ALLOWED) == set(S)


def test_terminal_states_are_terminal():
    assert machine.ALLOWED[S.COMPLETED] == set()
    assert machine.ALLOWED[S.CANCELLED] == set()
    assert S.COMPLETED.is_terminal and S.FAILED.is_terminal


def test_legal_transition():
    assert machine.transition(S.READY, S.AUTO_EXECUTING) is S.AUTO_EXECUTING


def test_self_transition_is_a_no_op():
    assert machine.transition(S.WAITING, S.WAITING) is S.WAITING


def test_illegal_transition_is_refused():
    with pytest.raises(machine.IllegalTransition):
        machine.transition(S.DISCOVERED, S.COMPLETED)


def test_a_loop_cannot_be_reopened_after_completion():
    for target in S:
        if target is S.COMPLETED:
            continue
        with pytest.raises(machine.IllegalTransition):
            machine.transition(S.COMPLETED, target)


def test_update_tool_refuses_an_illegal_jump(seeded, run_ctx):
    from closer.store.repository import Repo
    from closer.tools.loops import update_open_loop

    loop = Repo.loop("loop_aurora_chimney")
    result = update_open_loop(loop_id=loop.id, status="COMPLETED")
    assert result["ok"] is True  # WAITING -> COMPLETED is legal
    result = update_open_loop(loop_id=loop.id, status="DISCOVERED")
    assert result["ok"] is False
    assert "illegal loop transition" in result["summary"]


def test_update_tool_refuses_an_invented_status(seeded, run_ctx):
    from closer.tools.loops import update_open_loop

    result = update_open_loop(loop_id="loop_aurora_chimney", status="DEFINITELY_DONE")
    assert result["ok"] is False
    assert "not a loop status" in result["summary"]
