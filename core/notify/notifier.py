"""Notification Engine + throttle (plan §28).

- PROGRESS: at most one per `min_progress_interval_seconds` per task; updates
  suppressed in between are batched into the next one (batch_small_results).
  The soft cap grows for long tasks (+1 allowed update per 30 minutes).
- ERROR: identical error keys within `repeated_error_cooldown_seconds` are
  suppressed, unless the severity is higher than last time.
- Exempt types (approval, completion, failure, recovery, ...) are never held.
"""

from __future__ import annotations

import asyncio
import contextvars
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum

from channels.base import OutgoingMessage
from core.config.schema import NotificationThrottle
from core.log import get_logger

_log = get_logger("core")
# the task a notification belongs to (recorded with the message — plan §20 task chain)
_TASK: contextvars.ContextVar[int | None] = contextvars.ContextVar("nova_notify_task",
                                                                  default=None)

LONG_TASK_BONUS_EVERY_SECONDS = 1800


class MessageType(StrEnum):
    PROGRESS = "progress"
    ERROR = "error"
    INFO = "info"
    APPROVAL_REQUIRED = "approval_required"
    USER_INPUT_REQUIRED = "user_input_required"
    CRITICAL_SECURITY_ALERT = "critical_security_alert"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED_PERMANENTLY = "task_failed_permanently"
    AGENT_GOING_OFFLINE = "agent_going_offline"
    RECOVERY_AFTER_RESTART = "recovery_after_restart"


Sender = Callable[[str, OutgoingMessage], Awaitable[None]]


@dataclass
class _TaskProgress:
    first_at: float
    last_sent_at: float | None = None
    sent: int = 0
    pending: list[str] = field(default_factory=list)


class Notifier:
    def __init__(self, policy: NotificationThrottle, send: Sender,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.policy = policy
        self._send = send
        self._clock = clock
        self._exempt = {MessageType(t) for t in policy.exempt_message_types}
        self._progress: dict[int, _TaskProgress] = {}
        self._errors: dict[str, tuple[float, int]] = {}   # key -> (last_sent_at, severity)
        # Called after a task's final message is delivered (e.g. spoken reply).
        self.on_task_final: list[Callable[[str, MessageType, int], Awaitable[None]]] = []
        self._hooks: set[asyncio.Task[None]] = set()

    @staticmethod
    async def _run_hook(hook: Callable[[str, MessageType, int], Awaitable[None]],
                        chat_id: str, kind: MessageType, task_id: int) -> None:
        try:
            await hook(chat_id, kind, task_id)
        except Exception:
            _log.exception("task-final hook failed", extra={"action": "notify"})

    async def wait_hooks(self) -> None:
        """Wait for the task-final hooks still running (tests, shutdown)."""
        while self._hooks:
            await asyncio.gather(*list(self._hooks), return_exceptions=True)

    async def notify(self, chat_id: str, kind: MessageType, text: str, *,
                     task_id: int | None = None, error_key: str | None = None,
                     severity: int = 0,
                     buttons: list[list[tuple[str, str]]] | None = None,
                     photo: bytes | None = None,
                     video: tuple[str, bytes] | None = None) -> bool:
        """Returns True if the message was sent now."""
        token = _TASK.set(task_id)
        try:
            return await self._notify(chat_id, kind, text, task_id, error_key, severity,
                                      buttons, photo, video)
        finally:
            _TASK.reset(token)

    async def _notify(self, chat_id: str, kind: MessageType, text: str, task_id: int | None,
                      error_key: str | None, severity: int,
                      buttons: list[list[tuple[str, str]]] | None,
                      photo: bytes | None,
                      video: tuple[str, bytes] | None = None) -> bool:
        if kind in self._exempt or kind is MessageType.INFO:
            final_task = (task_id if kind in (MessageType.TASK_COMPLETED,
                                              MessageType.TASK_FAILED_PERMANENTLY) else None)
            if final_task is not None:
                self._progress.pop(final_task, None)
            sent = await self._deliver(chat_id, text, buttons, photo, video)
            if final_task is not None and sent:
                for hook in self.on_task_final:
                    # V1.1: in the background — a spoken answer (TTS can take 10–30 s)
                    # held the task engine, so the next voice command waited for it
                    job = asyncio.create_task(self._run_hook(hook, chat_id, kind, final_task))
                    self._hooks.add(job)
                    job.add_done_callback(self._hooks.discard)
            return sent
        if kind is MessageType.PROGRESS:
            return await self._progress_update(chat_id, text, task_id)
        return await self._error(chat_id, text, error_key or text, severity)

    def cap(self, task_id: int) -> int:
        p = self._progress.get(task_id)
        base = self.policy.soft_max_progress_updates_per_task
        if p is None:
            return base
        return base + int((self._clock() - p.first_at) // LONG_TASK_BONUS_EVERY_SECONDS)

    async def _progress_update(self, chat_id: str, text: str, task_id: int | None) -> bool:
        if task_id is None:
            return await self._deliver(chat_id, text, None)
        now = self._clock()
        p = self._progress.setdefault(task_id, _TaskProgress(first_at=now))
        interval_ok = (p.last_sent_at is None
                       or now - p.last_sent_at >= self.policy.min_progress_interval_seconds)
        if not interval_ok or p.sent >= self.cap(task_id):
            if self.policy.batch_small_results:
                p.pending.append(text)
            return False
        body = "\n".join([*p.pending, text]) if p.pending else text
        p.pending.clear()
        p.last_sent_at = now
        p.sent += 1
        return await self._deliver(chat_id, body, None)

    async def _error(self, chat_id: str, text: str, key: str, severity: int) -> bool:
        now = self._clock()
        last = self._errors.get(key)
        if last is not None:
            last_at, last_sev = last
            if now - last_at < self.policy.repeated_error_cooldown_seconds \
                    and severity <= last_sev:
                return False
        self._errors[key] = (now, severity)
        return await self._deliver(chat_id, text, None)

    async def _deliver(self, chat_id: str, text: str,
                       buttons: list[list[tuple[str, str]]] | None,
                       photo: bytes | None = None,
                       video: tuple[str, bytes] | None = None) -> bool:
        try:
            await self._send(chat_id, OutgoingMessage(text, buttons or [], photo,
                                                      task_id=_TASK.get(), video=video))
        except Exception:
            _log.exception("notification delivery failed", extra={"action": "notify"})
            return False
        return True
