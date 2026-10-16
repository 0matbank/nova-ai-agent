"""Schedules in the agent DB (plan §39: they survive a restart)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from core.db.models import Schedule, utcnow


@dataclass(frozen=True)
class ScheduleView:
    id: int
    kind: str
    action: str
    what: str
    rule: dict[str, Any] | None
    condition: dict[str, Any] | None
    next_run_at: datetime | None
    enabled: bool
    channel: str
    chat_id: str
    source: str | None
    run_count: int
    last_run_at: datetime | None
    last_task_id: int | None


def _view(r: Schedule) -> ScheduleView:
    return ScheduleView(r.id, r.kind, r.action, r.what, r.rule, r.condition, r.next_run_at,
                        r.enabled, r.channel, r.chat_id, r.source, r.run_count, r.last_run_at,
                        r.last_task_id)


class ScheduleStore:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def add(self, *, kind: str, action: str, what: str, next_run_at: datetime | None,
            channel: str, chat_id: str, rule: dict[str, Any] | None = None,
            condition: dict[str, Any] | None = None, source: str | None = "owner",
            request_text: str | None = None) -> ScheduleView:
        with self.sessions.begin() as s:
            row = Schedule(kind=kind, action=action, what=what, rule=rule, condition=condition,
                           next_run_at=next_run_at, enabled=True, channel=channel,
                           chat_id=chat_id, source=source, request_text=request_text,
                           run_count=0)
            s.add(row)
            s.flush()
            return _view(row)

    def get(self, sid: int) -> ScheduleView | None:
        with self.sessions() as s:
            r = s.get(Schedule, sid)
            return _view(r) if r is not None else None

    def active(self) -> list[ScheduleView]:
        with self.sessions() as s:
            rows = s.scalars(select(Schedule).where(Schedule.enabled.is_(True))
                             .order_by(Schedule.next_run_at, Schedule.id))
            return [_view(r) for r in rows]

    def by_source(self, source: str) -> ScheduleView | None:
        with self.sessions() as s:
            r = s.scalar(select(Schedule).where(Schedule.source == source))
            return _view(r) if r is not None else None

    def due(self, now: datetime) -> list[ScheduleView]:
        """Time-based jobs whose time has come (condition jobs are checked separately)."""
        with self.sessions() as s:
            rows = s.scalars(select(Schedule).where(
                Schedule.enabled.is_(True), Schedule.next_run_at.is_not(None),
                Schedule.next_run_at <= now).order_by(Schedule.next_run_at))
            return [_view(r) for r in rows]

    def conditions(self) -> list[ScheduleView]:
        with self.sessions() as s:
            rows = s.scalars(select(Schedule).where(Schedule.enabled.is_(True),
                                                    Schedule.kind == "condition"))
            return [_view(r) for r in rows]

    def fired(self, sid: int, next_run_at: datetime | None, task_id: int | None) -> None:
        """Record a run; a job without a next run is finished (disabled, kept for /schedules)."""
        with self.sessions.begin() as s:
            r = s.get(Schedule, sid)
            if r is None:
                return
            r.run_count += 1
            r.last_run_at = utcnow()
            r.last_task_id = task_id
            r.next_run_at = next_run_at
            r.enabled = next_run_at is not None
            r.updated_at = utcnow()

    def reschedule(self, sid: int, next_run_at: datetime | None) -> None:
        with self.sessions.begin() as s:
            r = s.get(Schedule, sid)
            if r is not None:
                r.next_run_at = next_run_at
                r.updated_at = utcnow()

    def update_static(self, sid: int, *, what: str, rule: dict[str, Any] | None,
                      next_run_at: datetime | None) -> None:
        with self.sessions.begin() as s:
            r = s.get(Schedule, sid)
            if r is not None:
                r.what, r.rule, r.next_run_at, r.enabled = what, rule, next_run_at, True
                r.updated_at = utcnow()

    def cancel(self, sid: int) -> bool:
        with self.sessions.begin() as s:
            r = s.get(Schedule, sid)
            if r is None or not r.enabled:
                return False
            r.enabled = False
            r.updated_at = utcnow()
            return True
