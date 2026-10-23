"""Read what an open app shows (owner 2026-10-23: "OBS এখন কি অবস্থা, কি দেখছ"): its
windows, any pop-up (title, text, buttons) and the main window's short text — through
UI Automation, for ANY app (no app or button names here). Read-only: acting inside an
app is the general computer-use engine's job (plan §71). Screen text is untrusted data.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from typing import Any

import desktop_apps
from desktop_screen import require_unlocked
from desktop_ui import _uia

WINDOW_BUTTONS = {"minimize", "maximize", "restore", "close", "float", "help"}
MAX_NODES = 1500
MAX_TEXTS = 12


def clean(label: str) -> str:
    return " ".join(label.replace("&", "").replace("…", "").replace("...", "").split()).lower()


def is_prompt(klass: str, automation_id: str, texts: list[str], buttons: list[str]) -> bool:
    """A pop-up asks something: text plus real answer buttons (not a dock / toolbar)."""
    tag = f"{klass} {automation_id}".lower()
    if "dock" in tag or "toolbar" in tag:
        return False
    answers = [b for b in buttons if clean(b) not in WINDOW_BUTTONS]
    return bool(answers) and (bool(texts) or "#32770" in tag or "dialog" in tag)


def _walk(root: Any, max_depth: int, into_windows: bool) -> Iterator[tuple[Any, int]]:
    stack = [(root, 0)]
    seen = 0
    while stack and seen < MAX_NODES:
        node, depth = stack.pop()
        seen += 1
        yield node, depth
        if depth >= max_depth:
            continue
        if depth > 0 and not into_windows and node.ControlTypeName == "WindowControl":
            continue
        try:
            children = node.GetChildren()
        except Exception:
            continue
        stack.extend((c, depth + 1) for c in reversed(children))


def _texts_buttons(win: Any) -> tuple[list[str], list[tuple[str, Any]]]:
    texts: list[str] = []
    buttons: list[tuple[str, Any]] = []
    for c, _ in _walk(win, 5, into_windows=False):
        try:
            kind, name = c.ControlTypeName, (c.Name or "").strip()
            if not name or c.IsOffscreen:
                continue
        except Exception:
            continue
        if kind == "TextControl" and len(texts) < MAX_TEXTS:
            texts.append(name[:200])
        elif kind == "ButtonControl":
            buttons.append((name, c))
    return texts, buttons


def _app_pids(name: str) -> tuple[str, dict[str, Any], set[int]]:
    key, app = desktop_apps.canonical(name)
    pids = {p.pid for p in desktop_apps.running(app["process"])} if app["process"] else set()
    return key, app, pids


def _top_windows(auto: Any, app: dict[str, Any], pids: set[int]) -> list[Any]:
    word = (app.get("name") or "").split()[0].lower() if app.get("name") else ""
    out = []
    for w in auto.GetRootControl().GetChildren():
        try:
            if not w.Name:
                continue
            if pids and w.ProcessId in pids:
                out.append(w)
            elif not pids and not app["process"] and word and word in w.Name.lower():
                out.append(w)              # Store app: no exe known — its titled window
        except Exception:
            continue
    return out


def _minimized(hwnd: int) -> bool:
    if sys.platform != "win32":
        return False
    import win32gui
    return bool(win32gui.IsIconic(hwnd))


def _scan(auto: Any, wins: list[Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """(pop-ups with their button controls, the main window's short text)."""
    prompts: list[dict[str, Any]] = []
    main_texts: list[str] = []
    for top in wins:
        candidates = [top] + [n for n, d in _walk(top, 4, into_windows=True)
                              if d > 0 and _is_window(n)]
        for w in candidates:
            try:
                texts, buttons = _texts_buttons(w)
                klass, aid = w.ClassName or "", w.AutomationId or ""
            except Exception:
                continue
            buttons = [(b, c) for b, c in buttons if clean(b) not in WINDOW_BUTTONS]
            labels = [b for b, _ in buttons]
            # a whole top window is a pop-up only when Windows says another window owns
            # it (a dialog of the app) — never by its button names
            whole = w is top and not _owned(top)
            if is_prompt(klass, aid, texts, labels) and not whole:
                prompts.append({"title": w.Name, "texts": texts, "buttons": labels,
                                "_controls": buttons})
            elif w is top and not main_texts:
                main_texts = texts
    return prompts, main_texts


def _owned(win: Any) -> bool:
    if sys.platform != "win32":
        return False
    import win32con
    import win32gui
    try:
        return bool(win32gui.GetWindow(win.NativeWindowHandle, win32con.GW_OWNER))
    except Exception:
        return False


def _is_window(node: Any) -> bool:
    try:
        return node.ControlTypeName == "WindowControl" and not node.IsOffscreen
    except Exception:
        return False


def view(name: str) -> dict[str, Any]:
    require_unlocked()
    key, app, pids = _app_pids(name)
    if app["process"] and not pids:
        return {"app": key, "name": app.get("name", key), "running": False,
                "windows": [], "prompts": [], "texts": []}
    with _uia() as auto:
        wins = _top_windows(auto, app, pids)
        prompts, texts = _scan(auto, wins)
        windows = []
        for w in wins:
            try:
                windows.append({"title": w.Name, "minimized": _minimized(w.NativeWindowHandle)})
            except Exception:
                continue
    return {"app": key, "name": app.get("name", key), "running": bool(pids or windows),
            "windows": windows, "texts": texts,
            "prompts": [{k: v for k, v in p.items() if k != "_controls"} for p in prompts]}
