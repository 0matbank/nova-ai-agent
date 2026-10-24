"""UI-TARS flag, end to end (plan §72, owner 2026-10-24: "no default change without a
real benchmark"): the full computer-use loop with the real local models on the
custom-drawn lab app (no accessibility at all → visual grounding is required), flag off
(qwen3-vl) vs. on (UI-TARS first). The raw click is RED: it is intercepted, never sent —
the measured thing is whether the point lands on the right drawn button, and how long
the whole task took to get there.

    uv run python scripts/grounding_e2e.py [rounds]
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "workers" / "desktop-worker")]

from core.config import load_config  # noqa: E402
from core.orchestrator.computer import ComputerUseAgent  # noqa: E402
from core.orchestrator.grounders import UiTarsGrounder  # noqa: E402
from core.queue.engine import ApprovalPending, TaskBlocked, TaskContext  # noqa: E402
from models.router import ProviderRouter  # noqa: E402
from providers.ollama_local.adapter import OllamaAdapter  # noqa: E402
from providers.provider_base import HealthState  # noqa: E402
from tests.mocks.cu_lab import lab_app  # noqa: E402
from tests.mocks.providers import FakeAdapter  # noqa: E402
from tests.mocks.skills import make_skill_env  # noqa: E402

TASKS = [("Render বোতামটা চাপো", "Render"), ("Pause চাপো", "Pause")]
BUTTONS = {"Render": 60, "Pause": 210}                     # tk_canvas.py, canvas px
CANVAS_BG = (0x20, 0x23, 0x2A)


def truth(image: Path, name: str) -> tuple[int, int, int, int]:
    from PIL import Image
    img = Image.open(image).convert("RGB")
    px = img.load()
    xs = [x for y in range(0, img.height, 2) for x in range(0, img.width, 2)
          if px[x, y][:3] == CANVAS_BG]
    ys = [y for y in range(0, img.height, 2) for x in range(0, img.width, 2)
          if px[x, y][:3] == CANVAS_BG]
    left, top, scale = min(xs), min(ys), (max(xs) - min(xs) + 2) / 520
    x = BUTTONS[name]
    return (round(left + x * scale), round(top + 120 * scale),
            round(left + (x + 120) * scale), round(top + 170 * scale))


async def one(engine: str, goal: str, name: str, tmp: Path) -> dict[str, Any]:
    cfg = load_config()
    from tests.mocks.desktop import real_desktop_client
    s = make_skill_env(tmp, {"desktop": real_desktop_client(tmp)})
    ad = {n: FakeAdapter(n, state=HealthState.UNAVAILABLE) for n in cfg.providers.providers}
    ad["ollama_local"] = OllamaAdapter(cfg.models)
    grounder = UiTarsGrounder(cfg.root, cfg.models.grounding.ui_tars) if engine == "ui-tars" \
        else None
    agent = ComputerUseAgent(s.runner, ProviderRouter(cfg.providers, ad), grounder)
    clicks: list[dict[str, Any]] = []
    real = agent._invoke

    async def spy(ctx: Any, skill: str, tool: str, params: dict[str, Any]) -> Any:
        if tool == "click_xy":
            clicks.append(params)
            raise TaskBlocked("intercepted: a raw click is RED (never sent in the bench)")
        return await real(ctx, skill, tool, params)
    agent._invoke = spy                                        # type: ignore[method-assign]
    plan0 = agent._plan

    async def plan(*a: Any, **kw: Any) -> Any:
        d = await plan0(*a, **kw)
        print("   PLAN", json.dumps(d, ensure_ascii=False)[:200], flush=True)
        return d
    agent._plan = plan                                         # type: ignore[method-assign]
    t = s.tasks.store.create(title=goal, request_text=goal, task_type="user_request",
                             channel="telegram", chat_id="555")
    ctx = TaskContext(s.tasks.store.get(t.id), s.tasks.store, s.tasks.engine.notifier,
                      s.tasks.permissions, s.tasks.approvals)
    t0 = time.perf_counter()
    try:
        with lab_app("canvas") as title:
            try:
                await agent.run(ctx, goal, None, "bn", window=title)
                outcome = "finished without a click"
            except (TaskBlocked, ApprovalPending) as e:
                outcome = str(e)[:80]
            import desktop_observe as ob
            obs = ob.observe(tmp / "shot", window=title, image=True)
    finally:
        if grounder is not None:
            grounder.stop()
    secs = time.perf_counter() - t0
    box = truth(Path(obs["image"]), name)
    ox, oy = obs["origin"]
    hit = bool(clicks) and box[0] <= clicks[0]["x"] - ox <= box[2] and \
        box[1] <= clicks[0]["y"] - oy <= box[3]
    return {"engine": engine, "goal": goal, "hit": hit, "secs": round(secs, 1),
            "point": clicks[0] if clicks else None, "outcome": outcome}


async def main(rounds: int, engines: tuple[str, ...] = ("vision", "ui-tars")) -> None:
    rows = []
    for _ in range(rounds):
        for engine in engines:
            for goal, name in TASKS:
                tmp = load_config().path("data_dir") / "benchmarks" / "grounding" / "e2e" / \
                    f"{engine}-{int(time.time() * 1000)}"
                r = await one(engine, goal, name, tmp)
                rows.append(r)
                print(f"{'✓' if r['hit'] else '✗'} {engine:8} {r['secs']:6.1f}s {goal} | "
                      f"{r['outcome'][:60]}", flush=True)
    for engine in ("vision", "ui-tars"):
        mine = [r for r in rows if r["engine"] == engine]
        hits = sum(r["hit"] for r in mine)
        avg = sum(r["secs"] for r in mine) / max(1, len(mine))
        print(f"{engine}: {hits}/{len(mine)} on the right button, average {avg:.1f}s per task")
    out = load_config().path("data_dir") / "benchmarks" / "grounding" / "e2e-result.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")           # type: ignore[union-attr]
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 2,
                     tuple(sys.argv[2].split(",")) if len(sys.argv) > 2 else
                     ("vision", "ui-tars")))
