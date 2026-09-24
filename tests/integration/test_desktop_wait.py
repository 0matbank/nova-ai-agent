"""PC locked → WAITING_DESKTOP → auto-resume after unlock (plan §5, §26);
/screenshot over Telegram."""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import io
from pathlib import Path

from channels.base import IncomingMessage
from core.ipc.client import WorkerDesktopLocked, WorkerUnavailable
from core.orchestrator.screen_commands import LOCKED_REPLY, NO_WORKER_REPLY, ScreenCommands
from core.queue.desktop_watch import DesktopWatcher
from core.queue.engine import TaskContext, Verdict
from core.queue.states import TaskState
from core.skills.desktop import NEEDS_DESKTOP, DesktopUnavailable
from tests.mocks.desktop import FakeDesktop
from tests.mocks.tasks import make_task_env


class NeedsDesktop:
    def __init__(self) -> None:
        self.locked = True
        self.ran: list[int] = []

    async def plan(self, ctx: TaskContext) -> list[str]:
        return ["background part", "desktop part"]

    async def run(self, ctx: TaskContext) -> str:
        async def bg(prev):  # type: ignore[no-untyped-def]
            self.ran.append(1)
            return {"bg": True}

        async def desk(prev):  # type: ignore[no-untyped-def]
            if self.locked:
                raise DesktopUnavailable("locked")
            self.ran.append(2)
            return {"desk": True}
        await ctx.run_step(1, bg)
        await ctx.run_step(2, desk)
        return "done"

    async def verify(self, ctx: TaskContext, result: str) -> Verdict:
        return Verdict(True, "ok")


def test_locked_pc_waits_then_resumes(tmp_path: Path) -> None:
    env = make_task_env(tmp_path / "a.db", tmp_path / "bk")
    ex = NeedsDesktop()
    env.engine.register("demo", ex)
    tid = env.store.create(title="t", request_text="t", task_type="demo",
                           channel="telegram", chat_id="555").id
    asyncio.run(env.engine.run_once())
    t = env.store.get(tid)
    assert t.state is TaskState.WAITING_DESKTOP and t.error_message == "locked"
    assert ex.ran == [1]                                   # background part done
    assert any(NEEDS_DESKTOP in text for _, text in env.sent)

    session = {"ok": True, "locked": True}
    watcher = DesktopWatcher(env.store, env.engine,
                             FakeDesktop({"/v1/session": lambda _: dict(session)}), None)
    assert asyncio.run(watcher.check()) == []              # still locked
    session["locked"] = False
    ex.locked = False
    assert asyncio.run(watcher.check()) == [tid]
    asyncio.run(env.engine.run_once())
    assert env.store.get(tid).state is TaskState.COMPLETED
    assert ex.ran == [1, 2]                                # step 1 not repeated


def test_watcher_ignores_absent_worker(tmp_path: Path) -> None:
    env = make_task_env(tmp_path / "a.db", tmp_path / "bk")
    tid = env.store.create(title="t", request_text="t", task_type="demo",
                           channel="telegram", chat_id="555").id
    env.store.transition(tid, TaskState.PLANNING)
    env.store.transition(tid, TaskState.RUNNING)
    env.store.transition(tid, TaskState.WAITING_DESKTOP)
    dead = DesktopWatcher(env.store, env.engine,
                          FakeDesktop({"/v1/session": WorkerUnavailable("x")}), None)
    assert asyncio.run(dead.check()) == []
    assert env.store.get(tid).state is TaskState.WAITING_DESKTOP


def _msg() -> IncomingMessage:
    return IncomingMessage("telegram", "1", "1", "1", "/screenshot", dt.datetime.now(dt.UTC))


def _jpeg() -> str:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, "JPEG")
    return base64.b64encode(buf.getvalue()).decode()


def test_screenshot_command_variants() -> None:
    ok = FakeDesktop({"/v1/screenshot": {"width": 1920, "height": 1080, "monitors": 1,
                                         "blank": False, "preview_jpeg_b64": _jpeg()}})
    out = asyncio.run(ScreenCommands(ok).screenshot(_msg(), []))
    assert out.photo and out.photo[:2] == b"\xff\xd8" and "1920×1080" in out.text
    locked = FakeDesktop({"/v1/screenshot": WorkerDesktopLocked("x")})
    assert asyncio.run(ScreenCommands(locked).screenshot(_msg(), [])).text == LOCKED_REPLY
    gone = FakeDesktop({"/v1/screenshot": WorkerUnavailable("x")})
    assert asyncio.run(ScreenCommands(gone).screenshot(_msg(), [])).text == NO_WORKER_REPLY
    assert asyncio.run(ScreenCommands(None).screenshot(_msg(), [])).text == NO_WORKER_REPLY


def test_screenshot_sent_as_photo_over_telegram(tmp_path: Path) -> None:
    from channels.telegram.auth import ChatWhitelist
    from channels.telegram.channel import TelegramChannel
    from core.log import setup_logging, shutdown_logging
    from core.orchestrator.commands import CommandRouter, HealthSources
    from tests.mocks.telegram import OWNER, FakeTelegram

    setup_logging(tmp_path / "logs", ["core", "audit"], worker="t", console=False)
    try:
        desk = FakeDesktop({"/v1/screenshot": {"width": 800, "height": 600, "monitors": 1,
                                               "blank": False, "preview_jpeg_b64": _jpeg()}})
        router = CommandRouter(tmp_path, HealthSources(), "x", screen=ScreenCommands(desk))
        fake = FakeTelegram()
        ch = TelegramChannel(fake.api(), ChatWhitelist([str(OWNER)]), router.handle,
                             poll_timeout=1)
        fake.push_message("/screenshot")
        asyncio.run(ch.poll_once())
        [photo] = fake.photos
        assert photo["chat_id"] == str(OWNER) and photo["size"] > 100
        assert "800×600" in photo["caption"]
    finally:
        shutdown_logging()
