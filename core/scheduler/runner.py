"""The scheduler loop (plan §39, Phase 19): every few seconds it starts the jobs whose
time has come (as normal tasks — same agents, same permissions and approvals) or sends
the reminder, and checks condition jobs. Everything is in the DB, so a restart picks
up exactly where it was.

Missed while Nova was off (plan §33 PC OFF): a job missed by less than
`grace_hours` still runs, marked late; older than that it is not run, the owner is
told; a recurring job then continues at its next time.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from core.log import get_logger
from core.notify.notifier import MessageType, Notifier
from core.queue.store import TaskStore
from core.scheduler.parse import next_occurrence, parse_schedule
from core.scheduler.store import ScheduleStore, ScheduleView

_log = get_logger("core")


class Scheduler:
    def __init__(self, store: ScheduleStore, tasks: TaskStore, notifier: Notifier | None,
                 timezone: str, wake: Callable[[], None] | None = None,
                 interval_seconds: float = 15, grace_hours: float = 12) -> None:
        self.store = store
        self.tasks = tasks
        self.notifier = notifier
        self.tz = ZoneInfo(timezone)
        self.wake = wake
        self.interval = interval_seconds
        self.grace = timedelta(hours=grace_hours)

    def local(self, dt: datetime) -> datetime:
        return dt.astimezone(self.tz)

    # --------------------------------------------------------- static (config)

    def sync_static(self, entries: list[dict[str, object]], owner_chat: str | None,
                    channel: str = "telegram") -> list[int]:
        """config/schedules.yaml: [{name, when, do}] — the owner's fixed jobs, kept in the
        DB like any other (updated when the file changes)."""
        ids = []
        now = datetime.now(self.tz)
        for e in entries:
            name, when, do = str(e.get("name", "")), str(e.get("when", "")), str(e.get("do", ""))
            if not (name and when and do and owner_chat):
                _log.warning(f"schedules.yaml entry skipped (needs name/when/do): {e}",
                             extra={"action": "scheduler.config"})
                continue
            spec = parse_schedule(when, now)
            if spec is None or spec.first_run is None:
                _log.warning(f"schedules.yaml '{name}': cannot read the time {when!r}",
                             extra={"action": "scheduler.config"})
                continue
            nxt = spec.first_run.astimezone(UTC)
            existing = self.store.by_source(f"config:{name}")
            if existing is None:
                ids.append(self.store.add(kind=spec.kind, action="task", what=do,
                                          next_run_at=nxt, channel=channel, chat_id=owner_chat,
                                          rule=spec.rule, source=f"config:{name}",
                                          request_text=f"{when} → {do}").id)
            else:
                self.store.update_static(existing.id, what=do, rule=spec.rule,
                                         next_run_at=existing.next_run_at
                                         if existing.enabled and existing.rule == spec.rule
                                         else nxt)
                ids.append(existing.id)
        return ids

    # ------------------------------------------------------------------- loop

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.tick()
            except Exception:  # noqa: BLE001 - the scheduler must never stop the service
                _log.exception("scheduler tick failed", extra={"action": "scheduler.tick"})
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self.interval)

    async def tick(self, now: datetime | None = None) -> list[int]:
        now = now or datetime.now(UTC)
        fired = []
        for job in self.store.due(now):
            assert job.next_run_at is not None
            late = now - job.next_run_at
            if late > self.grace:
                await self._missed(job, now)
                continue
            await self._fire(job, now, late_by=late if late > timedelta(minutes=2) else None)
            fired.append(job.id)
        for job in self.store.conditions():
            reason = self._condition_met(job)
            if reason:
                await self._fire(job, now, condition_text=reason)
                fired.append(job.id)
        return fired

    def _condition_met(self, job: ScheduleView) -> str:
        c = job.condition or {}
        if c.get("type") == "task_finished":
            t = self.tasks.get(int(c.get("task_id", 0)))
            if t is not None and t.is_terminal:
                return f"Task #{t.id} শেষ হয়েছে ({t.state})"
        elif c.get("type") == "file_exists":
            p = Path(str(c.get("path", "")))
            if p.exists():
                return f"{p} এসেছে"
        return ""

    def _next(self, job: ScheduleView, now: datetime) -> datetime | None:
        if job.kind != "recurring" or not job.rule:
            return None
        nxt = next_occurrence(job.rule, self.local(now))
        return nxt.astimezone(UTC) if nxt is not None else None

    async def _missed(self, job: ScheduleView, now: datetime) -> None:
        assert job.next_run_at is not None
        when = self.local(job.next_run_at).strftime("%d %b %H:%M")
        nxt = self._next(job, now)
        self.store.reschedule(job.id, nxt) if nxt else self.store.fired(job.id, None, None)
        _log.info(f"schedule #{job.id} missed ({when})", extra={"action": "scheduler.missed"})
        await self._tell(job, f"⏰ Schedule #{job.id} ({job.what[:80]}) {when}-এ চলার কথা ছিল, "
                              "কিন্তু Nova তখন বন্ধ ছিল — অনেক দেরি হয়ে যাওয়ায় চালাইনি।"
                              + (f" পরের বার: {self.local(nxt).strftime('%d %b %H:%M')}"
                                 if nxt else ""))

    async def _fire(self, job: ScheduleView, now: datetime, late_by: timedelta | None = None,
                    condition_text: str = "") -> None:
        late = (f" (দেরিতে — {int(late_by.total_seconds() // 60)} মিনিট; Nova তখন বন্ধ ছিল)"
                if late_by else "")
        nxt = self._next(job, now)
        task_id: int | None = None
        if job.action == "remind":
            head = f"⏰ {condition_text}\n" if condition_text else "⏰ মনে করিয়ে দিচ্ছি: "
            await self._tell(job, f"{head}{job.what}{late}")
        else:
            task = self.tasks.create(title=f"⏰ {job.what}"[:200], request_text=job.what,
                                     task_type="user_request", channel=job.channel,
                                     chat_id=job.chat_id)
            task_id = int(task.id)
            await self._tell(job, f"⏰ Schedule #{job.id}: “{job.what[:120]}” শুরু করলাম — "
                                  f"Task #{task_id}{late}"
                                  + (f"\n{condition_text}" if condition_text else ""))
            if self.wake is not None:
                self.wake()
        self.store.fired(job.id, nxt, task_id)
        _log.info(f"schedule #{job.id} fired ({job.action})" + (f" → task #{task_id}"
                                                               if task_id else ""),
                  extra={"action": "scheduler.fire", "task_id": task_id})

    async def _tell(self, job: ScheduleView, text: str) -> None:
        if self.notifier is not None:
            await self.notifier.notify(job.chat_id, MessageType.INFO, text)
