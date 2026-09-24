"""Persistent task store (plan §26, §29). Every state change goes through the
state machine inside a DB transaction and is logged to logs/tasks."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from core.db.models import Message, Setting, Task, TaskStep, utcnow
from core.log import get_logger
from core.queue.states import (
    ACTIVE,
    RUNNABLE,
    TERMINAL,
    InvalidTransition,
    TaskState,
    can_transition,
)

_log = get_logger("tasks")
QUEUE_PAUSED_KEY = "queue.paused"


class StepStatus(StrEnum):
    PENDING = "PENDING"
    CURRENT = "CURRENT"
    DONE = "DONE"
    FAILED = "FAILED"


@dataclass(frozen=True)
class StepView:
    seq: int
    title: str
    status: StepStatus
    checkpoint: dict[str, Any] | None
    error: str | None


@dataclass(frozen=True)
class TaskView:
    id: int
    title: str
    request_text: str
    task_type: str
    channel: str
    chat_id: str
    state: TaskState
    paused_from: TaskState | None
    priority: int
    retry_count: int
    cancel_requested: bool
    pause_requested: bool
    result_summary: str | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    steps: tuple[StepView, ...]

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL


def _view(t: Task) -> TaskView:
    return TaskView(
        id=t.id, title=t.title, request_text=t.request_text, task_type=t.task_type,
        channel=t.channel, chat_id=t.chat_id, state=TaskState(t.state),
        paused_from=TaskState(t.paused_from) if t.paused_from else None,
        priority=t.priority, retry_count=t.retry_count,
        cancel_requested=t.cancel_requested, pause_requested=t.pause_requested,
        result_summary=t.result_summary, error_code=t.error_code,
        error_message=t.error_message, created_at=t.created_at, updated_at=t.updated_at,
        started_at=t.started_at, finished_at=t.finished_at,
        steps=tuple(StepView(s.seq, s.title, StepStatus(s.status), s.checkpoint, s.error)
                    for s in t.steps),
    )


class CancelOutcome(StrEnum):
    CANCELLED = "cancelled"          # stopped immediately
    REQUESTED = "requested"          # running; stops at its next checkpoint
    ALREADY_FINISHED = "already_finished"
    NOT_FOUND = "not_found"


class TaskStore:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    # ------------------------------------------------------------ tasks

    def create(self, *, title: str, request_text: str, task_type: str, channel: str,
               chat_id: str, priority: int = 0) -> TaskView:
        with self._sessions.begin() as s:
            t = Task(title=title[:200], request_text=request_text, task_type=task_type,
                     channel=channel, chat_id=chat_id, state=TaskState.RECEIVED,
                     priority=priority)
            s.add(t)
            s.flush()
            s.refresh(t)
            view = _view(t)
        _log.info(f"task created: {view.title}",
                  extra={"task_id": view.id, "action": "task.create", "status": view.state})
        return view

    def get(self, task_id: int) -> TaskView | None:
        with self._sessions() as s:
            t = s.get(Task, task_id)
            return _view(t) if t else None

    def list_recent(self, limit: int = 10) -> list[TaskView]:
        with self._sessions() as s:
            rows = s.scalars(select(Task).order_by(Task.id.desc()).limit(limit))
            return [_view(t) for t in rows]

    def list_in(self, states: Iterable[TaskState]) -> list[TaskView]:
        with self._sessions() as s:
            rows = s.scalars(select(Task).where(Task.state.in_(list(states))).order_by(Task.id))
            return [_view(t) for t in rows]

    def next_runnable(self, task_types: Iterable[str]) -> TaskView | None:
        types = list(task_types)
        if not types:
            return None
        with self._sessions() as s:
            t = s.scalars(
                select(Task)
                .where(Task.state.in_(list(RUNNABLE)), Task.task_type.in_(types))
                .order_by(Task.priority.desc(), Task.id)
                .limit(1)
            ).first()
            return _view(t) if t else None

    def transition(self, task_id: int, dst: TaskState, **fields: Any) -> TaskView:
        with self._sessions.begin() as s:
            t = s.get(Task, task_id)
            if t is None:
                raise KeyError(f"task #{task_id} not found")
            src = TaskState(t.state)
            if not can_transition(src, dst):
                raise InvalidTransition(task_id, src, dst)
            now = utcnow()
            t.state = dst
            if dst is TaskState.PAUSED:
                t.paused_from = src
            elif src is TaskState.PAUSED:
                t.paused_from = None
            if dst is TaskState.RUNNING and t.started_at is None:
                t.started_at = now
            if dst in TERMINAL:
                t.finished_at = now
                t.cancel_requested = False
                t.pause_requested = False
            if dst is TaskState.PAUSED:
                t.pause_requested = False
            for k, v in fields.items():
                if not hasattr(Task, k):
                    raise AttributeError(f"unknown task field {k!r}")
                setattr(t, k, v)
            s.flush()
            s.refresh(t)
            view = _view(t)
        _log.info(f"{src} -> {dst}", extra={"task_id": task_id, "action": "task.transition",
                                            "status": dst})
        return view

    def request_cancel(self, task_id: int) -> CancelOutcome:
        with self._sessions.begin() as s:
            t = s.get(Task, task_id)
            if t is None:
                return CancelOutcome.NOT_FOUND
            state = TaskState(t.state)
            if state in TERMINAL:
                return CancelOutcome.ALREADY_FINISHED
            if state in ACTIVE:
                t.cancel_requested = True
                outcome = CancelOutcome.REQUESTED
            else:
                outcome = CancelOutcome.CANCELLED
        if outcome is CancelOutcome.CANCELLED:
            self.transition(task_id, TaskState.CANCELLED, error_code="CANCELLED_BY_USER")
        else:
            _log.info("cancel requested", extra={"task_id": task_id, "action": "task.cancel",
                                                 "status": "requested"})
        return outcome

    def flags(self, task_id: int) -> tuple[bool, bool]:
        """(cancel_requested, pause_requested)"""
        with self._sessions() as s:
            t = s.get(Task, task_id)
            return (t.cancel_requested, t.pause_requested) if t else (True, False)

    # ------------------------------------------------------------ steps

    def ensure_plan(self, task_id: int, titles: list[str]) -> tuple[StepView, ...]:
        """Create steps once. An existing plan is kept so a resumed task
        continues from its checkpoints instead of starting over (§29)."""
        with self._sessions.begin() as s:
            t = s.get(Task, task_id)
            if t is None:
                raise KeyError(f"task #{task_id} not found")
            if not t.steps:
                for i, title in enumerate(titles, 1):
                    t.steps.append(TaskStep(seq=i, title=title[:200]))
                s.flush()
                s.refresh(t)
            return _view(t).steps

    def _step(self, s: Session, task_id: int, seq: int) -> TaskStep:
        step = s.scalars(select(TaskStep).where(TaskStep.task_id == task_id,
                                                TaskStep.seq == seq)).first()
        if step is None:
            raise KeyError(f"task #{task_id} has no step {seq}")
        return step

    def start_step(self, task_id: int, seq: int) -> None:
        with self._sessions.begin() as s:
            step = self._step(s, task_id, seq)
            step.status = StepStatus.CURRENT
            step.started_at = utcnow()
            step.error = None

    def complete_step(self, task_id: int, seq: int, checkpoint: dict[str, Any] | None) -> None:
        with self._sessions.begin() as s:
            step = self._step(s, task_id, seq)
            step.status = StepStatus.DONE
            step.checkpoint = checkpoint
            step.finished_at = utcnow()
        _log.info(f"step {seq} done", extra={"task_id": task_id, "action": "task.checkpoint",
                                             "status": "ok"})

    def fail_step(self, task_id: int, seq: int, error: str) -> None:
        with self._sessions.begin() as s:
            step = self._step(s, task_id, seq)
            step.status = StepStatus.FAILED
            step.error = error[:2000]
            step.finished_at = utcnow()

    # --------------------------------------------------------- recovery

    def recover_after_restart(self) -> list[TaskView]:
        """Tasks that were mid-work when the process died → RETRYING, so the
        engine resumes them from their last checkpoint (§29, §32)."""
        recovered = []
        for task in self.list_in(ACTIVE):
            with self._sessions.begin() as s:
                for step in s.scalars(select(TaskStep).where(
                        TaskStep.task_id == task.id, TaskStep.status == StepStatus.CURRENT)):
                    step.status = StepStatus.PENDING
            if task.cancel_requested:
                recovered.append(self.transition(task.id, TaskState.CANCELLED,
                                                 error_code="CANCELLED_BY_USER"))
            else:
                recovered.append(self.transition(task.id, TaskState.RETRYING,
                                                 error_code="INTERRUPTED_BY_RESTART"))
        return recovered

    # ------------------------------------------------------ queue pause

    def is_queue_paused(self) -> bool:
        with self._sessions() as s:
            row = s.get(Setting, QUEUE_PAUSED_KEY)
            return bool(row and row.value)

    def pause_queue(self) -> list[int]:
        """Stop dispatching; active tasks pause at their next checkpoint."""
        self._set(QUEUE_PAUSED_KEY, True)
        with self._sessions.begin() as s:
            rows = list(s.scalars(select(Task).where(Task.state.in_(list(ACTIVE)))))
            for t in rows:
                t.pause_requested = True
            return [t.id for t in rows]

    def resume_queue(self) -> list[int]:
        self._set(QUEUE_PAUSED_KEY, False)
        with self._sessions.begin() as s:
            for t in s.scalars(select(Task).where(Task.pause_requested.is_(True))):
                t.pause_requested = False
        resumed = [t.id for t in self.list_in([TaskState.PAUSED])]
        for tid in resumed:
            self.transition(tid, TaskState.RETRYING, error_code=None)
        return resumed

    def _set(self, key: str, value: Any) -> None:
        with self._sessions.begin() as s:
            row = s.get(Setting, key)
            if row is None:
                s.add(Setting(key=key, value=value))
            else:
                row.value = value

    # --------------------------------------------------------- messages

    def record_message(self, *, channel: str, chat_id: str, direction: str, text: str,
                       external_id: str | None = None, task_id: int | None = None) -> None:
        """`text` must already be redacted by the caller."""
        with self._sessions.begin() as s:
            s.add(Message(channel=channel, chat_id=chat_id, direction=direction,
                          text=text, external_id=external_id, task_id=task_id))

    def count_messages(self) -> int:
        with self._sessions() as s:
            return len(list(s.scalars(select(Message.id))))
