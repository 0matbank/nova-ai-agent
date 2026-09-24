from __future__ import annotations

import asyncio

from channels.base import OutgoingMessage
from core.config.schema import EXEMPT_MESSAGE_TYPES, NotificationThrottle
from core.notify.notifier import LONG_TASK_BONUS_EVERY_SECONDS, MessageType, Notifier


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def make(batch: bool = True) -> tuple[Notifier, list[str], Clock]:
    sent: list[str] = []

    async def send(chat_id: str, msg: OutgoingMessage) -> None:
        sent.append(msg.text)

    clock = Clock()
    policy = NotificationThrottle(
        min_progress_interval_seconds=30, soft_max_progress_updates_per_task=5,
        repeated_error_cooldown_seconds=300, batch_small_results=batch,
        exempt_message_types=list(EXEMPT_MESSAGE_TYPES))
    return Notifier(policy, send, clock), sent, clock


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_progress_interval_and_batching() -> None:
    n, sent, clock = make()
    assert run(n.notify("c", MessageType.PROGRESS, "a", task_id=1))
    clock.t += 10
    assert not run(n.notify("c", MessageType.PROGRESS, "b", task_id=1))
    clock.t += 25
    assert run(n.notify("c", MessageType.PROGRESS, "c", task_id=1))
    assert sent == ["a", "b\nc"]          # suppressed "b" batched into the next one


def test_soft_cap_and_long_task_growth() -> None:
    n, sent, clock = make()
    for i in range(8):
        run(n.notify("c", MessageType.PROGRESS, f"p{i}", task_id=7))
        clock.t += 31
    assert len(sent) == 5
    clock.t += LONG_TASK_BONUS_EVERY_SECONDS
    assert run(n.notify("c", MessageType.PROGRESS, "late", task_id=7))


def test_exempt_messages_never_throttled() -> None:
    n, sent, _ = make()
    for kind in [MessageType.APPROVAL_REQUIRED, MessageType.APPROVAL_REQUIRED,
                 MessageType.TASK_COMPLETED, MessageType.CRITICAL_SECURITY_ALERT,
                 MessageType.RECOVERY_AFTER_RESTART, MessageType.AGENT_GOING_OFFLINE,
                 MessageType.USER_INPUT_REQUIRED, MessageType.TASK_FAILED_PERMANENTLY]:
        assert run(n.notify("c", kind, kind.value, task_id=1))
    assert len(sent) == 8


def test_repeated_error_suppressed_unless_severity_rises() -> None:
    n, sent, clock = make()
    assert run(n.notify("c", MessageType.ERROR, "disk low", error_key="disk", severity=1))
    clock.t += 60
    assert not run(n.notify("c", MessageType.ERROR, "disk low", error_key="disk", severity=1))
    assert run(n.notify("c", MessageType.ERROR, "disk CRITICAL", error_key="disk", severity=2))
    clock.t += 301
    assert run(n.notify("c", MessageType.ERROR, "disk CRITICAL", error_key="disk", severity=2))
    assert len(sent) == 3


def test_delivery_failure_does_not_raise() -> None:
    async def boom(chat_id: str, msg: OutgoingMessage) -> None:
        raise RuntimeError("telegram down")
    _, _, clock = make()
    n = Notifier(make()[0].policy, boom, clock)
    assert run(n.notify("c", MessageType.TASK_COMPLETED, "x")) is False
