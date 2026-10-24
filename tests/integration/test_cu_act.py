"""Plan §72 CU-3 — act on exactly the element that was observed, or refuse.
Real private test apps (tests/mocks/cu_lab.py) on this desktop — UIA patterns only, no
mouse, never the owner's apps. Plus pure image→screen geometry (any monitor layout)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "workers" / "desktop-worker"))

WIN = pytest.mark.skipif(sys.platform != "win32", reason="Windows UI Automation")


def _status(r: dict[str, Any]) -> str:
    return next(e["name"] for e in r["elements"] if e["automation_id"] == "Status")


def _saves(r: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted((e for e in r["elements"] if e["name"] == "Save"),
                  key=lambda e: e["rect"][0])


@WIN
def test_same_label_acts_on_the_one_that_was_chosen(tmp_path: Path) -> None:
    pytest.importorskip("uiautomation")
    import desktop_act as da
    import desktop_observe as ob

    from tests.mocks.cu_lab import lab_app
    with lab_app("dup") as title:
        r = ob.observe(tmp_path, window=title, image=False)
        first, second = _saves(r)
        out = da.act(r["observation_id"], second["id"], "click")
        assert out["delivered"] and out["effect"] == "changed" and out["method"]
        assert _status(ob.observe(tmp_path, window=title, image=False)) == "second"
        out = da.act(r["observation_id"], first["id"], "click")
        assert _status(ob.observe(tmp_path, window=title, image=False)) == "first"


@WIN
def test_a_replaced_element_with_the_same_label_is_refused(tmp_path: Path) -> None:
    pytest.importorskip("uiautomation")
    import desktop_act as da
    import desktop_observe as ob
    from desktop_ui import UIError

    from tests.mocks.cu_lab import lab_app
    with lab_app("dup") as title:
        r = ob.observe(tmp_path, window=title, image=False)
        first = _saves(r)[0]
        swap = next(e for e in r["elements"] if e["name"] == "Swap")
        da.act(r["observation_id"], swap["id"], "click")          # a NEW "Save" now
        with pytest.raises(UIError, match="stale_target"):
            da.act(r["observation_id"], first["id"], "click")     # old token: refused
        now = ob.observe(tmp_path, window=title, image=False)
        assert _status(now) == "swapped"                          # nothing was pressed
        # a fresh observation acts on the new button
        da.act(now["observation_id"], _saves(now)[0]["id"], "click")
        assert _status(ob.observe(tmp_path, window=title, image=False)) == "replacement"


@WIN
def test_a_moved_or_closed_window_is_refused(tmp_path: Path) -> None:
    pytest.importorskip("uiautomation")
    import desktop_act as da
    import desktop_observe as ob
    import win32gui
    from desktop_ui import UIError

    from tests.mocks.cu_lab import lab_app
    with lab_app("dup") as title:
        r = ob.observe(tmp_path, window=title, image=False)
        save = _saves(r)[0]
        hwnd = r["window"]["hwnd"]
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        win32gui.MoveWindow(hwnd, left + 80, top + 40, right - left, bottom - top, True)
        with pytest.raises(UIError, match="stale_target: the element moved"):
            da.act(r["observation_id"], save["id"], "click")
        assert _status(ob.observe(tmp_path, window=title, image=False)) == "idle"
        keep = r["observation_id"]
    with pytest.raises(UIError, match="stale_target"):
        da.act(keep, save["id"], "click")                        # the window is gone
    with pytest.raises(UIError, match="stale_target"):
        da.act("no-such-observation", 1, "click")


@WIN
def test_set_text_is_read_back_and_disabled_or_unknown_is_refused(tmp_path: Path) -> None:
    pytest.importorskip("uiautomation")
    import desktop_act as da
    import desktop_observe as ob
    from desktop_ui import UIError

    from tests.mocks.cu_lab import lab_app
    with lab_app("dup") as title:
        r = ob.observe(tmp_path, window=title, image=False)
        box = next(e for e in r["elements"] if e["name"] == "Notes")
        out = da.act(r["observation_id"], box["id"], "set_text", "hello from Nova")
        assert out["effect"] == "changed" and out["after"]["value"] == "hello from Nova"
        with pytest.raises(UIError, match="unknown op"):
            da.act(r["observation_id"], box["id"], "delete_everything")
        with pytest.raises(UIError, match="no element"):
            da.act(r["observation_id"], 999, "click")


# ------------------------------------------------------------------ geometry

def test_image_to_screen_on_any_monitor_layout() -> None:
    from desktop_geometry import Frame, GeometryError, center, inside
    # a window on a monitor LEFT of the main one (negative origin), image downscaled 2x
    f = Frame.captured(origin=(-1920, 100), size=(1600, 900), image_size=(800, 450))
    assert f.to_screen(0, 0) == (-1920, 100)
    assert f.to_screen(400, 225) == (-1120, 550)
    assert f.normalized_to_screen(500, 500) == (-1120, 550)
    assert f.contains(*f.to_screen(799, 449))
    for bad in [(800, 0), (-1, 5), (5, 450)]:
        with pytest.raises(GeometryError):
            f.to_screen(*bad)
    with pytest.raises(GeometryError):
        f.normalized_to_screen(1001, 10)
    # 150 % scaling: the captured physical size is larger than the logical window
    g = Frame.captured(origin=(0, 0), size=(1500, 1200), image_size=(1000, 800))
    assert g.to_screen(500, 400) == (750, 600)
    assert center((10, 20, 30, 60)) == (20, 40) and inside((20, 40), (10, 20, 30, 60))
    with pytest.raises(GeometryError):
        center((5, 5, 5, 9))


# ------------------------------------------------------------ permission decisions

def _act_tool():  # type: ignore[no-untyped-def]
    import importlib.util
    path = Path(__file__).resolve().parents[2] / "skills" / "desktop-ui" / "tools" / "act.py"
    spec = importlib.util.spec_from_file_location("act_tool", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod            # as the skill registry does (pydantic types)
    spec.loader.exec_module(mod)
    return mod


def test_act_permissions_are_decided_on_what_is_there_now(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    from types import SimpleNamespace

    from core.skills.api import ToolResult
    mod = _act_tool()
    seen: dict[str, Any] = {}

    async def fake_call(env: Any, path: str, body: Any = None) -> Any:
        return seen["reply"]
    monkeypatch.setattr(mod, "desktop_call", fake_call)
    env = SimpleNamespace(paths=SimpleNamespace(backups=tmp_path))

    def target(name: str, value: str = "", title: str = "Export", runs_cmds: bool = False
               ) -> Any:
        return {"element": {"type": "Button", "name": name, "automation_id": "",
                            "value": value, "enabled": True},
                "window": {"title": title, "process": "x.exe", "class": "",
                           "is_shell": runs_cmds}}

    def run(coro: Any) -> Any:
        return asyncio.run(coro)
    click = mod.P(observation_id="o", element=3, op="click")
    from core.skills.registry import SkillError
    seen["reply"] = ToolResult(False, "stale_target: moved")     # changed → look again,
    with pytest.raises(SkillError, match="stale_target"):        # not an approval
        run(mod.action(click, env))
    seen["reply"] = ToolResult(False, "desktop worker: timeout")  # cannot tell → RED
    assert run(mod.action(click, env)) == "ui.shell_input"
    seen["reply"] = target("Save")
    assert run(mod.action(click, env)) == "ui.click"
    assert run(mod.safety_check(click, env))[0] is True
    seen["reply"] = target("OK", title="Delete all files?")       # harmless label, risky dialog
    ok, why = run(mod.safety_check(click, env))
    assert ok is False and "delete" in why.lower()
    seen["reply"] = target("Run", runs_cmds=True)
    assert run(mod.action(click, env)) == "ui.shell_input"
    write = mod.P(observation_id="o", element=4, op="set_text", text="new")
    seen["reply"] = target("Notes", value="old text")
    assert run(mod.action(write, env)) == "ui.replace_text"
    ok, why = run(mod.safety_check(write, env))
    assert ok and "backed up" in why
    backup = next((tmp_path / "ui-text").glob("*.txt"))
    assert backup.read_text(encoding="utf-8") == "old text"
    seen["reply"] = target("Notes")
    assert run(mod.action(write, env)) == "ui.type"
    secret = target("PIN")
    secret["element"]["password"] = True                          # the app says: secret
    seen["reply"] = secret
    assert run(mod.action(write, env)) == "ui.type_secret"        # RED, whatever the app
    assert run(mod.action(click, env)) == "ui.click"              # only typing is secret


@WIN
def test_a_custom_editor_surface_is_listed_and_typed_into(tmp_path: Path) -> None:
    """CU-9 (found on real Notepad++/Scintilla): a focusable editing surface that offers
    no accessibility value is still listed — once — and typed into with the keyboard at
    its cursor; what was there stays."""
    pytest.importorskip("uiautomation")
    import tempfile

    import desktop_act as da
    import desktop_observe as ob

    from tests.mocks.cu_lab import lab_app
    with lab_app("editor") as title:
        r = ob.observe(tmp_path, window=title, image=False, focus=True)
        panes = [e for e in r["elements"] if e["type"] == "Pane"]
        assert len(panes) == 1 and panes[0]["ops"] == ["type", "focus"]
        assert panes[0]["actionable"]
        out = da.act(r["observation_id"], panes[0]["id"], "type", "Nova typed")
        assert out["delivered"] and out["method"] == "SendKeys(at end)"
        import time
        time.sleep(0.5)
        mirror = Path(tempfile.gettempdir()) / f"{title}.txt"
        text = mirror.read_text(encoding="utf-8")
        assert text == "first line\nsecond lineNova typed"        # added at the end
        mirror.unlink()
        with pytest.raises(da.UIError, match="type needs text"):
            da.act(r["observation_id"], panes[0]["id"], "type", "")


@WIN
def test_a_password_field_is_seen_in_a_real_app(tmp_path: Path) -> None:
    """CU-9: the toolkit's own IsPassword flag reaches the observation and the
    permission check — no field names or app names involved."""
    pytest.importorskip("uiautomation")
    import desktop_act as da
    import desktop_observe as ob

    from tests.mocks.cu_lab import lab_app
    with lab_app("dup") as title:
        obs = ob.observe(tmp_path, window=title, image=False)
        pin = next(e for e in obs["elements"] if e["name"] == "PIN")
        notes = next(e for e in obs["elements"] if e["name"] == "Notes")
        assert pin["password"] is True and notes["password"] is False
        assert not pin.get("value")                       # the secret is never read out
        t = da.target(obs["observation_id"], pin["id"])
        assert t["element"]["password"] is True


def test_the_place_to_type_is_a_fixed_key_sequence() -> None:
    """Fix 1 (CU-9 Notepad++ 0/6): the planner only names the place; Nova moves the
    cursor with standard editing keys — any editor."""
    import desktop_act as da
    assert da.place_keys("end") == "{Ctrl}{End}"
    assert da.place_keys("cursor") == ""
    assert da.place_keys("new_line", "first line") == "{Ctrl}{End}{Enter}"
    assert da.place_keys("new_line", "first line\n") == "{Ctrl}{End}"     # no empty line
    assert da.place_keys("new_line", "") == "{Ctrl}{End}{Enter}"          # unknown text
    assert da.place_keys("line_end:1") == "{Ctrl}{Home}{End}"
    assert da.place_keys("line_start:3") == "{Ctrl}{Home}{Down}{Down}{Home}"
    for bad in ("line_end:0", "line_end:x", "top", "end; rm"):
        with pytest.raises(da.UIError):
            da.place_keys(bad)


@WIN
@pytest.mark.parametrize(("at", "text", "want"), [
    ("new_line", "Nova done", "first line\nsecond line\nNova done"),
    ("line_end:1", ", eggs", "first line, eggs\nsecond line"),
    ("line_start:2", "> ", "first line\n> second line"),
])
def test_typing_lands_at_the_named_place(tmp_path: Path, at: str, text: str,
                                         want: str) -> None:
    pytest.importorskip("uiautomation")
    import tempfile
    import time

    import desktop_act as da
    import desktop_observe as ob

    from tests.mocks.cu_lab import lab_app
    with lab_app("editor") as title:
        r = ob.observe(tmp_path, window=title, image=False, focus=True)
        pane = next(e for e in r["elements"] if e["type"] == "Pane")
        da.act(r["observation_id"], pane["id"], "type", text, at)
        time.sleep(0.5)
        mirror = Path(tempfile.gettempdir()) / f"{title}.txt"
        got = mirror.read_text(encoding="utf-8")
        mirror.unlink()
    assert got == want
