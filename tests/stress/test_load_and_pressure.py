"""Phase 25 — load (many tasks at once, from several threads) and resource pressure."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from core.queue.engine import TaskContext, Verdict, VerdictStatus
from tests.mocks.tasks import make_task_env


class Counting:
    """A small two-step task that records every real execution of each step."""

    def __init__(self) -> None:
        self.runs: dict[int, int] = {}

    async def plan(self, ctx: TaskContext) -> list[str]:
        return ["work", "report"]

    async def run(self, ctx: TaskContext) -> str:
        async def work(prev: Any) -> dict[str, Any]:
            self.runs[ctx.task.id] = self.runs.get(ctx.task.id, 0) + 1
            await asyncio.sleep(0)
            return {"n": ctx.task.id}

        async def report(prev: Any) -> dict[str, Any]:
            return {"ok": True}
        await ctx.run_step(1, work)
        await ctx.checkpoint()
        await ctx.run_step(2, report)
        return f"done {ctx.task.id}"

    async def verify(self, ctx: TaskContext, result: str) -> Verdict:
        return Verdict(True, "", VerdictStatus.PASS)


def test_two_hundred_tasks_from_four_threads_each_run_exactly_once(tmp_path: Path) -> None:
    env = make_task_env(tmp_path / "load.db", tmp_path / "bk")
    ex = Counting()
    env.engine.register("load", ex)
    created: list[int] = []
    lock = threading.Lock()

    def producer(k: int) -> None:
        for i in range(50):
            t = env.store.create(title=f"p{k}-{i}", request_text="x", task_type="load",
                                 channel="telegram", chat_id="555")
            with lock:
                created.append(t.id)

    threads = [threading.Thread(target=producer, args=(k,)) for k in range(4)]
    t0 = time.time()

    async def consume() -> None:
        for th in threads:
            th.start()
        idle = 0
        while idle < 20:
            ran = await env.engine.run_once()
            idle = 0 if ran else idle + 1
            if not ran:
                await asyncio.sleep(0.02)
    asyncio.run(consume())
    for th in threads:
        th.join()
    asyncio.run(_drain(env))
    assert len(created) == 200 and len(set(created)) == 200
    states = {env.store.get(i).state.value for i in created}
    assert states == {"COMPLETED"}
    assert sorted(ex.runs) == sorted(created) and set(ex.runs.values()) == {1}
    assert time.time() - t0 < 120


async def _drain(env: Any) -> None:
    while await env.engine.run_once():
        pass


# --------------------------------------------------------- resource pressure

@pytest.fixture
def pressure(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    from core import resources
    from core.config import load_config
    from core.health import GpuStatus, PcStatus
    cfg = load_config()
    state = {"ram": 50.0, "vram": 10.0}

    async def fake_status(path: Path) -> PcStatus:
        return PcStatus(cpu_percent=5, ram_percent=state["ram"], ram_used_gb=1,
                        ram_total_gb=16, disk_free_gb=50, disk_total_gb=100, boot_time=0,
                        gpus=[GpuStatus("gpu", 10, state["vram"] * 160, 16000, 50)])
    monkeypatch.setattr(resources, "pc_status", fake_status)
    monkeypatch.setattr(resources, "top_memory", lambda n=3: "ollama 5 GB")
    p = resources.ResourcePressure(cfg.resources, Path("."))
    return p, state


def test_memory_pressure_relieves_once_alerts_once_and_recovers(pressure, caplog) -> None:  # type: ignore[no-untyped-def]
    p, state = pressure
    relieved: list[str] = []

    async def relieve() -> None:
        relieved.append("unload idle model")
    p.relievers.append(relieve)
    asyncio.run(p.refresh())
    assert p.level == "normal" and relieved == []
    state["ram"] = 97.0                                  # something eats the RAM
    with caplog.at_level("WARNING"):
        asyncio.run(p.refresh())
    assert p.level == "critical" and relieved == ["unload idle model"]
    criticals = [r for r in caplog.records if r.levelname == "CRITICAL"]
    assert len(criticals) == 1 and "ollama 5 GB" in criticals[0].getMessage()
    state["ram"] = 91.0                                  # 2 points under: still critical
    asyncio.run(p.refresh())
    assert p.level == "critical"
    state["ram"] = 60.0
    asyncio.run(p.refresh())
    assert p.level == "moderate" or p.level == "normal"
    caplog.clear()
    state["ram"] = 97.0                                  # flaps back within 30 min
    with caplog.at_level("WARNING"):
        asyncio.run(p.refresh())
    assert not [r for r in caplog.records if r.levelname == "CRITICAL"]   # phone stays quiet


def test_critical_memory_moves_heavy_local_ai_work_to_the_cloud(pressure) -> None:  # type: ignore[no-untyped-def]
    from models.router import ProviderRouter
    from providers.provider_base import HealthState, ProviderRequest
    from tests.mocks.providers import FakeAdapter
    p, state = pressure
    from core.config import load_config
    cfg = load_config()
    adapters: dict[str, Any] = {n: FakeAdapter(n, state=HealthState.HEALTHY) for n in
                                ("openai_codex", "google_antigravity", "gemini_api")}
    adapters["anthropic_claude"] = FakeAdapter("anthropic_claude", state=HealthState.DISABLED)
    adapters["ollama_local"] = FakeAdapter("ollama_local", ["local"] * 10)
    adapters["ollama_local"] = FakeAdapter("ollama_local", ["local"] * 10, capabilities=frozenset(
        {"vision", "quick_answer", "planning", "fallback", "simple"}))
    adapters["gemini_api"] = FakeAdapter("gemini_api", ["cloud"] * 10, capabilities=frozenset(
        {"vision", "quick_answer"}))
    r = ProviderRouter(cfg.providers, adapters, pressure=p)
    vision = ProviderRequest(task_id=1, task_type="vision", user_request="x")
    quick = ProviderRequest(task_id=1, task_type="quick_answer", user_request="x")
    assert asyncio.run(r.complete(vision)).provider == "ollama_local"        # local first
    state["ram"] = 97.0
    asyncio.run(p.refresh())
    # a heavy extra local model (qwen3-vl) would need more memory: cloud instead …
    assert asyncio.run(r.complete(vision)).provider == "gemini_api"
    # … while light work on the model already in memory stays local
    assert asyncio.run(r.complete(quick)).provider == "ollama_local"
