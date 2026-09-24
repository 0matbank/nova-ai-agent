"""Task state machine (plan §26).

Main path: RECEIVED → PLANNING → RUNNING → (WAITING_APPROVAL) → VERIFYING → COMPLETED
COMPLETED is only reachable from VERIFYING — "Verifier ছাড়া task complete নয়" (§30, §47).
RETRYING means "will (re)run from its last checkpoint" — used for retries,
/resume, and recovery after a restart.
"""

from __future__ import annotations

from enum import StrEnum


class TaskState(StrEnum):
    RECEIVED = "RECEIVED"
    PLANNING = "PLANNING"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    PAUSED = "PAUSED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    WAITING_DESKTOP = "WAITING_DESKTOP"
    WAITING_RESOURCE = "WAITING_RESOURCE"
    RETRYING = "RETRYING"


S = TaskState
TERMINAL = frozenset({S.COMPLETED, S.FAILED, S.CANCELLED})
# Picked up by the dispatcher.
RUNNABLE = frozenset({S.RECEIVED, S.RETRYING})
# Work was in progress; after a crash/restart these resume from the last checkpoint.
ACTIVE = frozenset({S.PLANNING, S.RUNNING, S.VERIFYING})
# Waiting on something outside the task; untouched by restart recovery.
WAITING = frozenset({S.WAITING_APPROVAL, S.WAITING_DESKTOP, S.WAITING_RESOURCE, S.PAUSED})

_ANY_EXIT = {S.FAILED, S.CANCELLED}

TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    S.RECEIVED: frozenset({S.PLANNING, S.PAUSED, *_ANY_EXIT}),
    S.PLANNING: frozenset({S.RUNNING, S.WAITING_APPROVAL, S.WAITING_RESOURCE,
                           S.PAUSED, S.RETRYING, *_ANY_EXIT}),
    S.RUNNING: frozenset({S.VERIFYING, S.WAITING_APPROVAL, S.WAITING_DESKTOP,
                          S.WAITING_RESOURCE, S.PAUSED, S.RETRYING, *_ANY_EXIT}),
    S.WAITING_APPROVAL: frozenset({S.RUNNING, S.RETRYING, S.PAUSED, *_ANY_EXIT}),
    S.WAITING_DESKTOP: frozenset({S.RUNNING, S.RETRYING, S.PAUSED, *_ANY_EXIT}),
    S.WAITING_RESOURCE: frozenset({S.RUNNING, S.RETRYING, S.PAUSED, *_ANY_EXIT}),
    S.VERIFYING: frozenset({S.COMPLETED, S.RETRYING, S.PAUSED, *_ANY_EXIT}),
    S.RETRYING: frozenset({S.RUNNING, S.PAUSED, *_ANY_EXIT}),
    S.PAUSED: frozenset({S.RETRYING, *_ANY_EXIT}),
    S.COMPLETED: frozenset(),
    S.FAILED: frozenset(),
    S.CANCELLED: frozenset(),
}


class InvalidTransition(Exception):
    def __init__(self, task_id: int, src: TaskState, dst: TaskState) -> None:
        super().__init__(f"task #{task_id}: {src} -> {dst} is not allowed")
        self.src, self.dst = src, dst


def can_transition(src: TaskState, dst: TaskState) -> bool:
    return dst in TRANSITIONS[src]
