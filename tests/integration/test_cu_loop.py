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


# the independent judge's answer (CU-9): no PASS without it
YES = json.dumps({"achieved": "yes", "why": "the screen shows it"})


def act(n: int, op: str = "click", **kw: Any) -> str:
    return json.dumps({"action": "act", "element": n, "op": op, **kw})


def test_presses_the_right_one_and_proves_it(tmp_path: Path) -> None:
    from tests.mocks.cu_lab import lab_app
    goal = "দ্বিতীয় Save বোতামটা চাপো"
    s, agent, ad = setup(tmp_path, [
        lambda r: act(number_of(r, "Save", 1)),
        json.dumps({"action": "done", "summary": "pressed the second Save",
                    "expect": [{"text": "second", "present": True}]}),
        YES,
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
        YES,
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


# ------------------------------------------------------------ CU-6

def test_safe_batch_does_several_field_edits_in_one_step(tmp_path: Path) -> None:
    from tests.mocks.cu_lab import lab_app
    s, agent, ad = setup(tmp_path, [
        lambda r: json.dumps({"action": "batch", "steps": [
            {"element": number_of(r, "Notes"), "op": "set_text", "text": "batch ok"},
            {"element": number_of(r, "Save"), "op": "click"}]}),   # a press: refused
        lambda r: json.dumps({"action": "batch", "steps": [
            {"element": number_of(r, "Notes"), "op": "set_text", "text": "batch ok"}]}),
        json.dumps({"action": "done", "summary": "typed",
                    "expect": [{"label": "Notes", "value": "batch ok"}]}),
        YES,
    ])
    with lab_app("dup") as title:
        out = asyncio.run(agent.run(ctx_of(s, "write batch ok in Notes"),
                                    "write batch ok in Notes", None, "en", window=title))
        import desktop_observe as ob
        status = next(e["name"] for e in ob.observe(tmp_path, window=title, image=False)[
            "elements"] if e["automation_id"] == "Status")
    assert out["evidence"]["verified"] is True
    assert status == "idle"                         # the Save in the batch never ran


def test_after_a_restart_a_done_press_is_not_repeated(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.mocks.cu_lab import lab_app
    s, agent, _ = setup(tmp_path, [
        lambda r: act(number_of(r, "Save")),          # the model wants to press it again
        json.dumps({"action": "done", "summary": "saved",
                    "expect": [{"text": "idle", "present": True}]}),
        YES,
    ])
    before = [{"did": "click [6]", "result": "ok", "ok": True, "label": "Save"}]
    monkeypatch.setattr(TaskContext, "recalled", lambda self: {"history": before})
    with lab_app("dup") as title:
        out = asyncio.run(agent.run(ctx_of(s, "press Save"), "press Save", None, "en",
                                    window=title))
    assert out["evidence"]["verified"] is True        # status still "idle": no 2nd press


def test_text_that_was_already_there_proves_nothing() -> None:
    from core.orchestrator.computer import check_expect
    before = {"window": {"title": "w"}, "elements": [
        {"type": "CheckBox", "name": "Keep a backup", "checked": False}]}
    after = {"window": {"title": "w"}, "elements": [
        {"type": "CheckBox", "name": "Keep a backup", "checked": False}]}
    ok, _ = check_expect([{"text": "Keep a backup", "present": True}], after, before)
    assert ok is None                                   # always visible: not a proof
    ok, _ = check_expect([{"label": "Keep a backup", "checked": True}], after, before)
    assert ok is False                                  # the real state: not ticked
    ok, _ = check_expect([{"text": "saved", "present": True}],
                         {"window": {"title": "w"}, "elements": [{"name": "saved"}]}, before)
    assert ok is True                                   # new on screen: a proof


def test_editor_surface_empty_plan_retry_and_no_duplicate_typing(tmp_path: Path) -> None:
    """CU-9 (real Notepad++ run, 2026-10-24): an empty plan is retried WITHOUT thinking;
    'type' adds at the end of an editing surface; typing the same words again is refused
    instead of piling up copies."""
    import tempfile
    import time

    from tests.mocks.cu_lab import lab_app
    goal = "লেখার শেষে নতুন লাইনে 'Nova done' লেখো"

    def surface(r: ProviderRequest) -> int:
        return int(re.findall(r"\[(\d+)\] Pane", r.user_request)[0])
    typed = lambda r: act(surface(r), "type", text="\nNova done")    # noqa: E731
    s, agent, ad = setup(tmp_path, [
        "",                                   # a thinking model that answered nothing
        typed,                                # the retry (must be think-free)
        typed,                                # the same words again: refused
        json.dumps({"action": "done", "summary": "added the line", "expect": []}),
    ])
    with lab_app("editor") as title:
        out = asyncio.run(agent.run(ctx_of(s, goal), goal, None, "bn", window=title))
        time.sleep(0.5)
        mirror = Path(tempfile.gettempdir()) / f"{title}.txt"
        text = mirror.read_text(encoding="utf-8")
        mirror.unlink()
    reqs = ad["ollama_local"].requests
    assert reqs[0].think is None and reqs[1].think is False
    assert text == "first line\nsecond line\nNova done"        # once, at the end
    assert out["kind"] == "computer"


def test_the_planners_own_proof_needs_the_independent_judge(tmp_path: Path) -> None:
    """CU-9 false PASS (real Character Map, 2026-10-24): the planner typed the wrong
    words and then 'expected' exactly them. The judge sees only the goal and the screen,
    says no — not PASS; the corrected text then passes. (Wrong words no fixed rule catches:
    instruction words are already stopped before typing.)"""
    from tests.mocks.cu_lab import lab_app
    goal = "Notes ঘরে hello লেখো"
    no = json.dumps({"achieved": "no", "why": "the field holds more than hello"})
    s, agent, ad = setup(tmp_path, [
        lambda r: act(number_of(r, "Notes"), "set_text", text="hello there"),
        json.dumps({"action": "done", "summary": "typed",
                    "expect": [{"label": "Notes", "value": "hello there"}]}),
        no,
        lambda r: act(number_of(r, "Notes"), "set_text", text="hello"),
        json.dumps({"action": "done", "summary": "typed",
                    "expect": [{"label": "Notes", "value": "hello"}]}),
        YES,
    ])
    with lab_app("dup") as title:
        out = asyncio.run(agent.run(ctx_of(s, goal), goal, None, "bn", window=title))
    assert out["evidence"]["verified"] is True
    judge = [r for r in ad["ollama_local"].requests if "achieved" in r.system]
    assert len(judge) == 2 and all(r.think is False for r in judge)
    assert "typed" not in judge[0].user_request          # never the planner's own claim


def test_a_password_from_the_goal_needs_approval_in_any_field(tmp_path: Path) -> None:
    """CU-9 (real KeePassXC, 2026-10-24): the owner's password went into a plain
    description field. A value the goal gives as a password is RED wherever it goes."""
    from core.queue.engine import ApprovalPending
    from tests.mocks.cu_lab import lab_app
    secret = "Lab-Test-2468"
    goal = f"Notes ঘরে লেখো, password '{secret}'"
    s, agent, ad = setup(tmp_path, [
        lambda r: act(number_of(r, "Notes"), "set_text", text=secret)])
    ctx = ctx_of(s, "write the password into Notes")      # the stored request: no secret
    with lab_app("dup") as title, pytest.raises(ApprovalPending):
        asyncio.run(agent.run(ctx, goal, None, "bn", window=title))
    # Fix 2: the approval message, the task database and the model route never carry it
    assert s.tasks.sent and not any(secret in text for _, text in s.tasks.sent)
    assert secret.encode() not in s.tasks.db_path.read_bytes()
    assert all(r.privacy == "local_only" for r in ad["ollama_local"].requests)


@pytest.mark.parametrize(("goal", "found"), [
    ("password 'Lab-Test-2468'", ["Lab-Test-2468"]), ("PIN 4821 দিয়ে খোলো", ["4821"]),
    ("পাসওয়ার্ড হলো abc123", ["abc123"]), ("OTP: 778899", ["778899"]),
    ("typing in the spinner", []), ("pin the tab", []), ("reset the password field", []),
])
def test_secrets_named_in_a_goal(goal: str, found: list[str]) -> None:
    from core.orchestrator.computer import secrets_in
    assert secrets_in(goal) == found


def test_sensitive_text_is_found_and_masked() -> None:
    from core.orchestrator.computer import mask, sensitive
    assert sensitive("4111 1111 1111 1111", "")                  # a real card shape
    assert not sensitive("1234 5678 9012 3456", "")              # fails the card check
    assert sensitive("sk-ant-" + "b" * 30, "")                     # a key shape
    assert not sensitive("meeting at 5", "password 'x9-y7z'")
    assert mask("typed x9-y7z and 4111-1111-1111-1111", "password 'x9-y7z'") == \
        "typed ••• and •••"


@pytest.mark.parametrize(("goal", "text", "refused"), [
    ("Characters to copy ঘরে NOVA9 লেখো", "ঘরে NOVA9 লেখো", True),   # real false PASS
    ("Characters to copy ঘরে NOVA9 লেখো", "NOVA9", False),
    ("write hello in the Name box", "write hello", True),
    ("শেষে নতুন লাইনে 'Nova done' লেখো", "Nova done", False),
    ("শেষে নতুন লাইনে 'Nova done' লেখো", "Nova done Nova done", True),
    ("Title ঘরে 'Nova demo' লেখো আর Public চালু করো", "Nova demo", False),
    ("প্রথম লাইনের শেষে ', eggs' যোগ করো", "eggs", True),          # a part: real run
    ("প্রথম লাইনের শেষে ', eggs' যোগ করো", ", eggs", False),
])
def test_only_the_owners_words_are_typed(goal: str, text: str, refused: bool) -> None:
    from core.orchestrator.computer import text_problem
    assert (text_problem(goal, text) is not None) is refused


def test_the_judge_sees_an_unticked_box_as_unchecked() -> None:
    from core.orchestrator.computer import judge_lines
    lines = judge_lines({"elements": [
        {"type": "CheckBox", "name": "Advanced view", "checked": False},
        {"type": "CheckBox", "name": "Spell check", "checked": True},
        {"type": "Button", "name": "Close", "chrome": True}]})
    assert lines == ["CheckBox «Advanced view» unchecked", "CheckBox «Spell check» checked"]



@pytest.mark.parametrize(("goal", "place"), [
    ("এই লেখার একদম শেষে নতুন লাইনে 'Nova done' লেখো", "new_line"),
    ("প্রথম লাইনের শেষে ', eggs' যোগ করো", "line_end:1"),
    ("add 'x' at the end of line 3", "line_end:3"),
    ("দ্বিতীয় লাইনের শুরুতে '> ' দাও", "line_start:2"),
    ("শেষ লাইনের শেষে ok লেখো", "end"),
    ("Notes ঘরে hello লেখো", None),
])
def test_the_place_comes_from_the_owners_words(goal: str, place: str | None) -> None:
    """Fix 1b (real Notepad++: the planner forgot "at" and the text joined the last
    line): when the planner names no place, the owner's own words decide."""
    from core.orchestrator.computer import place_from_goal, unquote
    assert place_from_goal(goal) == place
    assert unquote("', eggs'") == ", eggs" and unquote("«hi»") == "hi"


def test_locate_never_clicks_to_check_text_that_is_readable(tmp_path: Path) -> None:
    """Fix 1b (real Notepad++: locate used to 'check' text became a raw click → RED):
    text already in the control list needs no click at all."""
    from tests.mocks.cu_lab import lab_app
    s, agent, _ = setup(tmp_path, [
        json.dumps({"action": "locate", "target": "'idle'", "why": "check it"}),
        json.dumps({"action": "done", "summary": "seen", "expect": []}),
    ])
    calls: list[str] = []
    real = agent._invoke

    async def spy(ctx: Any, skill: str, tool: str, params: dict[str, Any]) -> Any:
        calls.append(tool)
        return await real(ctx, skill, tool, params)
    agent._invoke = spy
    with lab_app("dup") as title:
        asyncio.run(agent.run(ctx_of(s, "is it idle?"), "is it idle?", None, "en",
                              window=title))
    assert "click_xy" not in calls and "act" not in calls



def _screen(text: str) -> dict[str, Any]:
    return {"window": {"title": "w"}, "elements": [
        {"type": "Pane", "name": text, "tail": text[-200:], "length": len(text)}]}


def test_fixed_proofs_of_what_nova_typed() -> None:
    """Fix 3: proofs that do not depend on any model — the typed text is there, not
    more often than asked, on its own line when a new line was asked."""
    from core.orchestrator.computer import typed_proofs
    goal = "শেষে নতুন লাইনে 'Nova done' লেখো"
    before = _screen("first line\r\nsecond line")
    typed = [{"did": "type [5]", "ok": True, "typed": "Nova done", "at": "new_line"}]
    ok, _ = typed_proofs(typed, _screen("first line\r\nsecond line\r\nNova done"),
                         before, goal)
    assert ok is True
    ok, why = typed_proofs(typed, _screen("first line\r\nsecond lineNova done"), before, goal)
    assert ok is False and any("নতুন লাইনে" in w for w in why)          # joined the line
    ok, why = typed_proofs(typed, _screen("first line\nsecond line\nNova done\nNova done"),
                           before, goal)
    assert ok is False and any("2 বার" in w for w in why)               # a duplicate
    ok, _ = typed_proofs(typed, before, before, goal)
    assert ok is False                                                  # not there at all
    assert typed_proofs([], before, before, goal) == (None, [])
    secret = [{"did": "set_text [3]", "ok": True, "typed": "•••"}]
    assert typed_proofs(secret, before, before, goal) == (None, [])     # never compared
    # the judge said yes 3/3 to ', eggs' at the END of the file when line 1 was asked
    goal = "প্রথম লাইনের শেষে ', eggs' যোগ করো"
    before = _screen("Shopping list: milk\r\n\r\nNOTE")
    eggs = [{"did": "type [5]", "ok": True, "typed": ", eggs", "at": "line_end:1"}]
    assert typed_proofs(eggs, _screen("Shopping list: milk, eggs\r\n\r\nNOTE"), before,
                        goal)[0] is True
    assert typed_proofs(eggs, _screen("Shopping list: milk\r\n\r\nNOTE, eggs"), before,
                        goal)[0] is False
    # a set_text that was replaced later: only the last value counts
    fixed = [{"did": "set_text [4]", "ok": True, "typed": "hello there", "label": "Notes"},
             {"did": "set_text [4]", "ok": True, "typed": "hello", "label": "Notes"}]
    notes = {"window": {"title": "w"}, "elements": [
        {"type": "Edit", "name": "Notes", "value": "hello"}]}
    assert typed_proofs(fixed, notes, None, "Notes ঘরে hello লেখো")[0] is True



def test_ui_tars_flag_is_off_by_default_and_parses_points() -> None:
    """Fix 4: UI-TARS is optional — the shipped config keeps the existing vision path."""
    from core.orchestrator.grounders import grounder_for, point_from_answer
    cfg = load_config()
    assert cfg.models.grounding.engine == "vision" and grounder_for(cfg) is None
    assert point_from_answer("(120,45)", (400, 300)) == (120, 45)
    assert point_from_answer("click(start_box='(10,20,30,40)')", (400, 300)) == (20, 30)
    assert point_from_answer("(999,45)", (400, 300)) is None        # outside: no guess
    assert point_from_answer("There are none.", (400, 300)) is None


@pytest.mark.parametrize("grounder_answers", [True, False])
def test_ui_tars_first_then_the_vision_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                            grounder_answers: bool) -> None:
    """With the flag on, locate asks UI-TARS first; no answer → the vision model (the
    existing path) still finds it. Either way the raw click stays RED (spied here)."""
    from PIL import Image

    from tests.mocks.cu_lab import lab_app

    def render_box(png: bytes) -> tuple[int, int, int, int]:
        import io
        img = Image.open(io.BytesIO(png)).convert("RGB")
        w, h = img.size
        xs, ys = [], []
        for y in range(0, h, 2):
            for x in range(0, w // 3, 2):
                red, green, blue = img.getpixel((x, y))
                if abs(red - 0x3B) < 12 and abs(green - 0x82) < 12 and abs(blue - 0xF6) < 12:
                    xs.append(x)
                    ys.append(y)
        return min(xs), min(ys), max(xs), max(ys)

    class FakeTars:
        calls = 0

        async def locate(self, png: bytes, size: tuple[int, int], target: str) -> Any:
            FakeTars.calls += 1
            if not grounder_answers:
                return None
            x1, y1, x2, y2 = render_box(png)
            return (x1 + x2) / 2, (y1 + y2) / 2

    def ground(r: ProviderRequest) -> str:
        import io
        x1, y1, x2, y2 = render_box(r.images[0])
        w, h = Image.open(io.BytesIO(r.images[0])).size
        return json.dumps({"bbox_2d": [x1 * 1000 / w, y1 * 1000 / h, x2 * 1000 / w,
                                       y2 * 1000 / h]})
    plan = json.dumps({"action": "locate", "target": "the blue Render button"})
    s, agent, ad = setup(tmp_path, [plan, ground])
    agent.grounder = FakeTars()
    clicks: list[dict[str, Any]] = []
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
        import desktop_observe as ob
        o = ob.observe(tmp_path, window=title, image=True)
    x1, y1, x2, y2 = render_box(Path(o["image"]).read_bytes())
    ox, oy = o["origin"]
    assert FakeTars.calls == 1 and len(clicks) == 1
    assert ox + x1 <= clicks[0]["x"] <= ox + x2 and oy + y1 <= clicks[0]["y"] <= oy + y2
    vision_calls = [r for r in ad["ollama_local"].requests if "bbox_2d" in r.user_request]
    assert len(vision_calls) == (0 if grounder_answers else 1)


def test_a_menu_item_named_in_the_goal_is_recognised() -> None:
    """Regression found on real Notepad++ (Find 3/3 → 0/2): 'Find...\tCtrl+F' is the
    'Find' the owner named — the mnemonic, ellipsis and shortcut are not its name."""
    from core.orchestrator.computer import menu_words
    assert menu_words("&Find...\tCtrl+F") == "find"
    assert menu_words("Export PDF…") == "export pdf"
    assert menu_words("Save") == "save"


def test_a_drawing_surface_is_not_clicked_like_a_button(tmp_path: Path) -> None:
    """Real canvas run: the planner 'clicked' the drawing surface itself forever. An op a
    control does not take is refused at once with the way forward (locate). (A click on
    a dropdown still expands it: checked on the real Character Map font task.)"""
    from tests.mocks.cu_lab import lab_app
    calls: list[str] = []
    s, agent, _ = setup(tmp_path, [
        lambda r: act(int(re.findall(r"\[(\d+)\] Pane", r.user_request)[0]), "click"),
        json.dumps({"action": "fail", "reason": "stop"})])
    real = agent._invoke

    async def spy(ctx: Any, skill: str, tool: str, params: dict[str, Any]) -> Any:
        calls.append(tool)
        return await real(ctx, skill, tool, params)
    agent._invoke = spy
    with lab_app("canvas") as title, pytest.raises(TaskBlocked):
        asyncio.run(agent.run(ctx_of(s, "press Render"), "press Render", None, "en",
                              window=title))
    assert "act" not in calls                       # refused before reaching the worker
