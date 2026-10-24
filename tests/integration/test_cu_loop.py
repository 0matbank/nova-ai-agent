"""Plan §72 CU-4 — the general computer-use loop on private lab apps: real windows, real
UI Automation, the real desktop worker code (in-process) and the real SkillRunner
(permissions, approvals). Only the AI is scripted — it reads the numbered control list
it is given, like a model would, so nothing here knows a button's number in advance."""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

from core.config import load_config
from core.queue.engine import TaskBlocked, TaskContext
from models.router import ProviderRouter
from providers.provider_base import HealthState, ProviderRequest
from tests.mocks.providers import FakeAdapter

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows UI Automation")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "workers" / "desktop-worker"))


def number_of(request: ProviderRequest, label: str, nth: int = 0) -> int:
    """What a model does: find the control with this label in the list it was shown."""
    hits = re.findall(r"\[(\d+)\] \w+ «" + re.escape(label) + "»", request.user_request)
    return int(hits[nth])


def setup(tmp_path: Path, script: list[Any]) -> tuple[Any, Any, dict[str, FakeAdapter]]:
    from core.orchestrator.computer import ComputerUseAgent
    from tests.mocks.desktop import real_desktop_client
    from tests.mocks.skills import make_skill_env
    s = make_skill_env(tmp_path, {"desktop": real_desktop_client(tmp_path)})
    cfg = load_config()
    caps = frozenset({"planning", "vision", "reasoning", "simple"})
    ad = {n: FakeAdapter(n, state=HealthState.UNAVAILABLE) for n in cfg.providers.providers}
    ad["ollama_local"] = FakeAdapter("ollama_local", script, capabilities=caps)
    router = ProviderRouter(cfg.providers, ad)
    return s, ComputerUseAgent(s.runner, router), ad


def ctx_of(s: Any, goal: str) -> TaskContext:
    t = s.tasks.store.create(title=goal, request_text=goal, task_type="user_request",
                             channel="telegram", chat_id="555")
    task = s.tasks.store.get(t.id)
    return TaskContext(task, s.tasks.store, s.tasks.engine.notifier, s.tasks.permissions,
                       s.tasks.approvals)


def act(n: int, op: str = "click", **kw: Any) -> str:
    return json.dumps({"action": "act", "element": n, "op": op, **kw})


def test_presses_the_right_one_and_proves_it(tmp_path: Path) -> None:
    from tests.mocks.cu_lab import lab_app
    goal = "দ্বিতীয় Save বোতামটা চাপো"
    s, agent, ad = setup(tmp_path, [
        lambda r: act(number_of(r, "Save", 1)),
        json.dumps({"action": "done", "summary": "pressed the second Save",
                    "expect": [{"text": "second", "present": True}]}),
    ])
    with lab_app("dup") as title:
        out = asyncio.run(agent.run(ctx_of(s, goal), goal, None, "bn", window=title))
    ev = out["evidence"]
    assert out["kind"] == "computer" and ev["verified"] is True, ev
    assert any("second" in c and "✓" in c for c in ev["checks"])
    # the model saw a numbered list, never a fixed button number
    assert "[" in ad["ollama_local"].requests[0].user_request


def test_a_claimed_done_that_the_screen_disproves_is_not_accepted(tmp_path: Path) -> None:
    from tests.mocks.cu_lab import lab_app
    done = json.dumps({"action": "done", "summary": "saved",
                       "expect": [{"text": "replacement", "present": True}]})
    s, agent, _ = setup(tmp_path, [done, done])
    with lab_app("dup") as title, pytest.raises(TaskBlocked, match="not visible"):
        asyncio.run(agent.run(ctx_of(s, "save"), "save", None, "en", window=title))


def test_done_without_a_checkable_result_is_unverified(tmp_path: Path) -> None:
    from tests.mocks.cu_lab import lab_app
    s, agent, _ = setup(tmp_path, [
        lambda r: act(number_of(r, "Notes"), "set_text", text="hello"),
        json.dumps({"action": "done", "summary": "typed it"}),
    ])
    with lab_app("dup") as title:
        out = asyncio.run(agent.run(ctx_of(s, "type hello"), "type hello", None, "en",
                                    window=title))
    assert out["evidence"]["verified"] is None                   # → UNVERIFIED, not PASS
    assert out["evidence"]["effects"]                            # the input was delivered


def test_checkbox_goal_is_read_back(tmp_path: Path) -> None:
    from tests.mocks.cu_lab import lab_app
    s, agent, _ = setup(tmp_path, [
        lambda r: act(number_of(r, "Keep a backup"), "toggle"),
        json.dumps({"action": "done", "summary": "backup on",
                    "expect": [{"label": "Keep a backup", "checked": True}]}),
    ])
    with lab_app("wpf") as title:
        out = asyncio.run(agent.run(ctx_of(s, "turn on backup"), "turn on backup", None,
                                    "en", window=title))
    assert out["evidence"]["verified"] is True, out["evidence"]


def test_no_progress_is_detected_and_stopped(tmp_path: Path) -> None:
    from tests.mocks.cu_lab import lab_app
    same = json.dumps({"action": "keys", "keys": "shift", "why": "nothing"})
    s, agent, _ = setup(tmp_path, [same] * 6)
    with lab_app("dup") as title, pytest.raises(TaskBlocked, match="no progress"):
        asyncio.run(agent.run(ctx_of(s, "x"), "x", None, "en", window=title))


def test_risky_choice_is_asked_and_escape_stops(tmp_path: Path) -> None:
    import desktop_guard

    from tests.mocks.cu_lab import lab_app
    s, agent, _ = setup(tmp_path, [
        json.dumps({"action": "ask", "question": "এটা মুছে ফেলবে — চালিয়ে যাব?"}),
    ])
    with lab_app("dup") as title:
        out = asyncio.run(agent.run(ctx_of(s, "x"), "x", None, "bn", window=title))
        assert out["needs_input"] and "মুছে" in out["answer"]
        s2, agent2, _ = setup(tmp_path / "b", [lambda r: act(number_of(r, "Save"))])
        desktop_guard.halt()                                     # the owner pressed Esc
        try:
            with pytest.raises(TaskBlocked, match="Escape"):
                asyncio.run(agent2.run(ctx_of(s2, "press Save"), "press Save", None, "en",
                                       window=title))
        finally:
            desktop_guard.clear_halt()


def test_a_drawn_button_is_found_by_vision_and_mapped_to_the_screen(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Custom-drawn app: nothing readable → the vision model sees the numbered screenshot
    and asks to locate; the grounder's box (here: computed from the real screenshot —
    a perfect grounder) is mapped to the exact screen point of the drawn button."""
    from PIL import Image

    from core.orchestrator import computer
    from tests.mocks.cu_lab import lab_app
    clicks: list[dict[str, Any]] = []
    seen_images: list[int] = []

    def plan(r: ProviderRequest) -> str:
        seen_images.append(len(r.images))
        return json.dumps({"action": "locate", "target": "the blue Render button"})

    def ground(r: ProviderRequest) -> str:
        import io
        img = Image.open(io.BytesIO(r.images[0])).convert("RGB")
        w, h = img.size
        xs, ys = [], []
        for y in range(0, h, 2):
            for x in range(0, w // 3, 2):                       # the left (first) button
                red, green, blue = img.getpixel((x, y))
                if abs(red - 0x3B) < 12 and abs(green - 0x82) < 12 and abs(blue - 0xF6) < 12:
                    xs.append(x)
                    ys.append(y)
        box = [min(xs) * 1000 / w, min(ys) * 1000 / h, max(xs) * 1000 / w,
               max(ys) * 1000 / h]
        return json.dumps({"bbox_2d": box})

    s, agent, _ = setup(tmp_path, [plan, ground])
    real_invoke = agent._invoke

    async def spy(ctx: Any, skill: str, tool: str, params: dict[str, Any]) -> Any:
        if tool == "click_xy":
            clicks.append(params)
            raise TaskBlocked("stop here: a raw click needs the owner's approval")
        return await real_invoke(ctx, skill, tool, params)
    monkeypatch.setattr(agent, "_invoke", spy)
    with lab_app("canvas") as title:
        with pytest.raises(TaskBlocked, match="approval"):
            asyncio.run(agent.run(ctx_of(s, "press Render"), "press Render", None, "en",
                                  window=title))
        from collections import Counter

        import mss
        x, y = clicks[0]["x"], clicks[0]["y"]
        with mss.MSS() as sct:                    # the screen around the mapped point
            area = sct.grab({"left": x - 10, "top": y - 10, "width": 21, "height": 21})
        colors = Counter(area.pixels[r][c] for r in range(21) for c in range(21))
    blue = sum(n for (r, g, b), n in colors.items()
               if abs(r - 0x3B) < 20 and abs(g - 0x82) < 20 and abs(b - 0xF6) < 20)
    assert seen_images[0] == 1                       # sparse → the planner saw the image
    assert blue > 21 * 21 // 2, colors.most_common(3)   # the drawn button (text aside)
    assert computer.MAX_STEPS >= 10


# ------------------------------------------------------------ rules from real runs

def test_same_label_controls_carry_their_position() -> None:
    from core.orchestrator.computer import element_lines
    obs = {"elements": [
        {"id": 6, "type": "Button", "name": "Save", "actionable": True, "ops": ["click"]},
        {"id": 7, "type": "Button", "name": "Save", "actionable": True, "ops": ["click"]},
        {"id": 8, "type": "ListItem", "name": "4K", "actionable": False, "ops": []},
        {"id": 9, "type": "ListItem", "name": "4K", "actionable": True, "ops": ["select"]},
    ]}
    lines = element_lines(obs)
    assert "[6] Button «Save» (1st of 2, ops: click)" in lines
    assert "[7] Button «Save» (2nd of 2, ops: click)" in lines
    assert not any(line.startswith("[8]") for line in lines)      # takes no action: hidden
    assert "[9] ListItem «4K» (ops: select)" in lines


def test_presses_only_what_the_goal_asks(tmp_path: Path) -> None:
    from core.orchestrator.computer import ComputerUseAgent

    class NoAI:                       # these rules must not depend on the model
        async def complete(self, request: ProviderRequest) -> Any:
            raise AssertionError("the model was asked")
    agent = ComputerUseAgent(None, NoAI())                 # type: ignore[arg-type]
    obs = {"elements": [{"id": 6, "type": "Button", "name": "Save"},
                        {"id": 7, "type": "Button", "name": "OK"}]}
    ctx = None
    click = {"action": "act", "op": "click"}

    def needed(el: int, goal: str, history: list[dict[str, Any]]) -> bool:
        return asyncio.run(agent._needed(ctx, {**click, "element": el}, obs, goal,
                                         history))                 # type: ignore[arg-type]
    pressed = [{"did": "click [6]", "ok": True, "label": "Save"}]
    assert needed(6, "press Save", [])                      # named once: yes
    assert not needed(6, "press Save", pressed)             # already pressed once: no
    assert needed(6, "press Save, then Save again", pressed)   # named twice: yes
    assert not needed(7, "turn on backup", [])              # unnamed confirm: never
    assert not needed(6, "turn on backup", [])
