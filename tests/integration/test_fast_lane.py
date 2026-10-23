"""V1.1 fast lane (live 2026-10-23): "প্রিমিয়ার প্রো খোলো" waited 2 minutes behind one slow
AI answer. Quick deterministic requests now have their own dispatcher."""

from __future__ import annotations

import asyncio
import datetime as dt
from pathlib import Path

from channels.base import IncomingMessage
from core.queue.engine import FAST_PRIORITY, TaskContext, Verdict, VerdictStatus
from core.queue.states import TaskState
from core.router.intent import is_quick_request
from tests.mocks.tasks import make_task_env


class Slow:
    """'question' blocks until released; anything else finishes at once."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.started: list[str] = []

    async def plan(self, ctx: TaskContext) -> list[str]:
        return ["do"]

    async def run(self, ctx: TaskContext) -> str:
        self.started.append(ctx.task.request_text)
        if ctx.task.request_text == "question":
            await self.release.wait()
        return "done"

    async def verify(self, ctx: TaskContext, result: str) -> Verdict:
        return Verdict(True, "", VerdictStatus.PASS)


def msg(text: str) -> IncomingMessage:
    return IncomingMessage("telegram", "555", "555", "1", text, dt.datetime.now(dt.UTC))


def test_quick_request_does_not_wait_behind_a_slow_one(tmp_path: Path) -> None:
    env = make_task_env(tmp_path / "a.db", tmp_path / "bk")
    slow = Slow()
    env.engine.register("user_request", slow)
    env.commands.quick = lambda text: text == "open premiere"

    async def go() -> None:
        stop = asyncio.Event()
        lanes = [asyncio.create_task(env.engine.run(stop)),
                 asyncio.create_task(env.engine.run(stop, fast=True))]
        await env.commands.submit(msg("question"))
        while "question" not in slow.started:
            await asyncio.sleep(0.01)
        quick = await env.commands.submit(msg("open premiere"))
        assert quick.task_id is not None
        assert env.store.get(quick.task_id).priority == FAST_PRIORITY
        for _ in range(300):                         # the slow task is still running
            if env.store.get(quick.task_id).state is TaskState.COMPLETED:
                break
            await asyncio.sleep(0.01)
        assert env.store.get(quick.task_id).state is TaskState.COMPLETED
        slow.release.set()
        for _ in range(300):
            if all(t.state is TaskState.COMPLETED for t in env.store.list_recent(5)):
                break
            await asyncio.sleep(0.01)
        stop.set()
        await asyncio.gather(*lanes)
    asyncio.run(go())
    assert slow.started == ["question", "open premiere"]      # each ran exactly once


def test_fast_lane_never_takes_slow_work(tmp_path: Path) -> None:
    env = make_task_env(tmp_path / "a.db", tmp_path / "bk")
    env.engine.register("user_request", Slow())
    asyncio.run(env.commands.submit(msg("explain quantum physics")))   # no quick check set
    assert not asyncio.run(env.engine.run_once(fast=True))
    assert asyncio.run(env.engine.run_once())


def test_what_counts_as_quick() -> None:
    for text in ["notepad kholo", "pc er obostha ki", "একটা স্ক্রিনশট পাঠাও",
                 "কোন কোন অ্যাপ খোলা আছে", "কী কী চলছে দেখাও", "chrome bondho koro"]:
        assert is_quick_request(text), text
    for text in ["বাংলাদেশের রাজধানী কী", "git demo te bug fix koro", "PC restart koro",
                 "screen-e ki error dekhacche", "30 minute por pc status bolo",
                 "D:\\a.mp4 theke reel banao"]:
        assert not is_quick_request(text), text
