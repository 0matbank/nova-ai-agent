"""Plan §71 CU-1 — the Observer reads ANY app's window: one private test app per UI
technology (tests/mocks/cu_lab.py), observed for real on this desktop, read-only.
PASS = the right controls are found, the frame is told apart from the content, apps
that expose nothing are flagged for vision, and each window takes < 1.5 s."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "workers" / "desktop-worker"))

# what each technology must show (names/values a person sees), and whether the tree is
# expected to be too poor to work with (→ vision, CU-2)
EXPECT: dict[str, tuple[set[str], bool]] = {
    "winforms": ({"Nova Input", "Nova Safe Button", "Delete Everything"}, False),
    "wpf": ({"draft", "Keep a backup", "First clip", "Second clip", "Save", "Cancel"}, False),
    "win32_dialog": ({"Yes", "No", "Cancel"}, False),
    "web": ({"Title", "Public", "Upload", "Discard", "Help"}, False),
    "tk": (set(), True),            # Tk draws its own buttons: unnamed in the tree
    "canvas": (set(), True),        # buttons are only pictures
}


def _labels(r: dict[str, Any]) -> set[str]:
    return {e["name"] or e.get("value", "") for e in r["elements"]
            if e["actionable"] and not e["chrome"]}


@pytest.mark.skipif(sys.platform != "win32", reason="Windows UI Automation")
@pytest.mark.parametrize("kind", list(EXPECT))
def test_observe_any_ui_technology(kind: str, tmp_path: Path) -> None:
    pytest.importorskip("uiautomation")
    import desktop_observe as ob

    from tests.mocks.cu_lab import lab_app
    want, sparse = EXPECT[kind]
    with lab_app(kind) as title:
        # the work loop brings the window to the front first: a fully covered
        # browser/Electron window shows no content (found 2026-10-24)
        r = ob.observe(tmp_path, window=title, focus=True)
    assert r["window"]["foreground"]
    assert r["window"]["title"] == title
    assert want <= _labels(r), (kind, r["sparse"], [(e["type"], e["name"]) for e in r["elements"]])
    assert r["sparse"] is sparse, kind
    assert r["timing_ms"]["total"] < 1500, r["timing_ms"]
    # the frame (minimize / maximize / close) is never taken for the app's content
    frame = [e for e in r["elements"] if e["name"].lower() in ob.FRAME_BUTTONS]
    assert frame and all(e["chrome"] for e in frame), frame
    # numbered 1…n in order; the marks image and the screenshot exist and are not blank
    assert [e["id"] for e in r["elements"]] == list(range(1, len(r["elements"]) + 1))
    assert Path(r["image"]).exists() and Path(r["marked"]).exists() and not r["blank"]


def _el(type_: str, name: str, rect: tuple[int, int, int, int], value: str = "") -> dict:
    return {"type": type_, "name": name, "value": value, "automation_id": "", "class": "",
            "enabled": True, "focused": False, "rect": rect}


def test_numbering_and_sparse_rules() -> None:
    import desktop_observe as ob
    win = (0, 0, 400, 300)
    els = ob.number([
        _el("TitleBar", "", (10, 0, 400, 30)),
        _el("Button", "Close", (360, -2, 401, 31)),            # sticks out of the bar
        _el("Button", "Save", (10, 200, 90, 230)),
        _el("Text", "Save", (30, 205, 70, 225)),               # the button's own label
        _el("Edit", "", (10, 60, 300, 80), value="draft"),
        _el("Button", "Off screen", (500, 500, 560, 520)),     # outside the window
        _el("Text", "Hello", (10, 100, 200, 120)),
        _el("Button", "", (10, 140, 60, 160)),
    ], win)
    names = [(e["type"], e["name"] or e["value"]) for e in els]
    assert ("Text", "Save") not in names and ("Button", "Off screen") not in names
    assert [e["id"] for e in els] == [1, 2, 3, 4, 5]
    assert next(e for e in els if e["name"] == "Close")["chrome"]
    # named, actionable, not frame: "Save" + the "draft" box = 2 → still sparse (< 3)
    assert ob.is_sparse(els)
    more = ob.number([_el("Button", n, (10, 40 * i, 90, 40 * i + 30))
                      for i, n in enumerate(["A", "B", "C"])], win)
    assert not ob.is_sparse(more)


def test_observe_tool_text() -> None:
    import importlib.util
    path = Path(__file__).resolve().parents[2] / "skills" / "desktop-ui" / "tools" / "observe.py"
    spec = importlib.util.spec_from_file_location("observe_tool", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    r = {"window": {"title": "Export", "process": "x.exe"}, "sparse": False, "elements": [
        {"id": 1, "type": "Button", "name": "Close", "chrome": True},
        {"id": 2, "type": "Edit", "name": "", "value": "draft", "chrome": False},
        {"id": 3, "type": "Button", "name": "Save", "chrome": False, "enabled": False}]}
    text = mod.describe(r)
    assert "[2] Edit «draft»" in text and "[3] Button «Save» (disabled)" in text
    assert "Close" not in text                                   # the frame is not listed
