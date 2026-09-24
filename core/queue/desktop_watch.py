"""Resume WAITING_DESKTOP tasks once the PC is unlocked (plan §5, §26, §32)."""

from __future__ import annotations

import asyncio
import contextlib

from core.ipc.client import WorkerClient, WorkerError
from core.log import get_logger
from core.notify.notifier import MessageType, Notifier
from core.queue.engine import TaskEngine
from core.queue.states import TaskState
from core.queue.store import TaskStore

_log = get_logger("tasks")


class DesktopWatcher:
    def __init__(self, store: TaskStore, engine: TaskEngine, client: WorkerClient | None,
                 notifier: Notifier | None, interval: float = 15.0) -> None:
        self.store = store
        self.engine = engine
        self.client = client
        self.notifier = notifier
        self.interval = interval

    async def check(self) -> list[int]:
        waiting = self.store.list_in([TaskState.WAITING_DESKTOP])
        if not waiting or self.client is None:
            return []
        try:
            session = await self.client.call("GET", "/v1/session")
        except WorkerError:
            return []                               # still not logged in
        if session.get("locked"):
            return []
        released = []
        for t in waiting:
            self.store.transition(t.id, TaskState.RETRYING, error_code=None, error_message=None)
            released.append(t.id)
            _log.info("desktop available again", extra={"task_id": t.id,
                                                         "action": "task.desktop_resume"})
            if self.notifier is not None:
                await self.notifier.notify(t.chat_id, MessageType.INFO,
                                           f"🔓 PC unlock হয়েছে — Task #{t.id} আবার চলছে।",
                                           task_id=t.id)
        self.engine.wake()
        return released

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.check()
            except Exception:
                _log.exception("desktop watcher failed", extra={"action": "task.desktop_watch"})
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self.interval)
