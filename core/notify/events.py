"""Live task events (plan §72 CU-7, Report F phase 6): what a running task really did,
step by step — received, observed, target found, action delivered, effect seen,
verified — for the dashboard and the live Telegram message. Only real executor events:
no invented percentages. Kept in memory (bounded); the task DB stays the record."""

from __future__ import annotations

import itertools
import threading
import time
from collections import OrderedDict, deque
from typing import Any

MAX_TASKS = 50
MAX_EVENTS_PER_TASK = 200
TYPES = ("task_received", "observation_ready", "target_grounded", "approval_required",
         "action_started", "action_delivered", "effect_observed", "verification_result",
         "paused", "stopped", "failed", "progress")


class TaskEvents:
    def __init__(self) -> None:
        self._tasks: OrderedDict[int, deque[dict[str, Any]]] = OrderedDict()
        self._seq = itertools.count(1)
        self._lock = threading.Lock()

    def add(self, task_id: int, kind: str, summary: str, **data: Any) -> dict[str, Any]:
        if kind not in TYPES:
            raise ValueError(f"unknown event type {kind!r}")
        event = {"seq": next(self._seq), "task_id": task_id, "type": kind,
                 "summary": summary[:300], "at": time.time(),
                 **{k: v for k, v in data.items() if v is not None}}
        with self._lock:
            q = self._tasks.get(task_id)
            if q is None:
                q = self._tasks[task_id] = deque(maxlen=MAX_EVENTS_PER_TASK)
                while len(self._tasks) > MAX_TASKS:
                    self._tasks.popitem(last=False)
            q.append(event)
            self._tasks.move_to_end(task_id)
        return event

    def since(self, task_id: int, after: int = 0) -> list[dict[str, Any]]:
        """Events of a task with seq > after (a reconnecting viewer sends its last seq,
        so nothing is shown twice)."""
        with self._lock:
            return [e for e in self._tasks.get(task_id, ()) if e["seq"] > after]


EVENTS = TaskEvents()
