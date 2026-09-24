from __future__ import annotations

import pytest

from core.queue.states import ACTIVE, RUNNABLE, TERMINAL, TRANSITIONS, TaskState, can_transition


def test_every_state_has_rules() -> None:
    assert set(TRANSITIONS) == set(TaskState)


def test_completed_only_after_verification() -> None:
    sources = [s for s in TaskState if can_transition(s, TaskState.COMPLETED)]
    assert sources == [TaskState.VERIFYING]


@pytest.mark.parametrize("terminal", sorted(TERMINAL))
def test_terminal_states_are_final(terminal: TaskState) -> None:
    assert TRANSITIONS[terminal] == frozenset()


def test_every_non_terminal_state_can_be_cancelled() -> None:
    for s in set(TaskState) - TERMINAL:
        assert can_transition(s, TaskState.CANCELLED), s


def test_main_path_is_valid() -> None:
    path = ["RECEIVED", "PLANNING", "RUNNING", "WAITING_APPROVAL", "RUNNING",
            "VERIFYING", "COMPLETED"]
    for a, b in zip(path, path[1:], strict=False):
        assert can_transition(TaskState(a), TaskState(b)), (a, b)


def test_groups_disjoint() -> None:
    assert not (RUNNABLE & ACTIVE) and not (TERMINAL & (RUNNABLE | ACTIVE))
