"""Windows UI Automation inside the user session (plan §17 priority order):

  1. UIA control patterns (Invoke / Toggle / SelectionItem / ExpandCollapse / Value)
  2. keyboard to the focused control (SendKeys)
  3. raw mouse coordinates — last resort, separate RED action

Every action returns what was observed afterwards so the caller can verify
the result (plan §54). Nothing here decides permissions — the core does.
"""

from __future__ import annotations

import contextlib
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from desktop_screen import require_unlocked

SHELL_PROCESSES = {"cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe",
                   "openconsole.exe", "conhost.exe", "wsl.exe", "bash.exe", "mintty.exe",
                   "wt.exe"}
CONSOLE_CLASSES = {"consolewindowclass", "cascadia_hosting_window_class",
                   "pseudoconsolewindow", "mintty", "virtualconsoleclass"}
# GUI framework windows hosted by a shell process (e.g. a WinForms form run
# from PowerShell) take no commands — they are not command sinks.
GUI_CLASS_PREFIXES = ("windowsforms10.", "hwndwrapper[")
MAX_NODES = 3000


class UIError(Exception):
    pass


@contextmanager
def _uia() -> Iterator[Any]:
    if sys.platform != "win32":
        raise UIError("UI Automation is Windows-only")
    import uiautomation as auto

    with auto.UIAutomationInitializerInThread(debug=False):
        yield auto


def _proc_name(pid: int) -> str:
    import psutil
    try:
        return str(psutil.Process(pid).name())
    except psutil.Error:
        return ""


def _describe_window(w: Any) -> dict[str, Any]:
    return {"title": w.Name, "hwnd": w.NativeWindowHandle, "pid": w.ProcessId,
            "process": _proc_name(w.ProcessId), "class": w.ClassName}


def _top_windows(auto: Any) -> list[Any]:
    out = []
    for w in auto.GetRootControl().GetChildren():
        # A hung or closing window can throw COMError on any property read; one bad
        # window must not break listing all the others (seen live 2026-09-25).
        try:
            if w.Name and not w.IsOffscreen:
                out.append(w)
        except Exception:
            continue
    return out


def list_windows() -> list[dict[str, Any]]:
    require_unlocked()
    with _uia() as auto:
        fg = auto.GetForegroundControl()
        fg_hwnd = fg.GetTopLevelControl().NativeWindowHandle if fg else None
        out = []
        for w in _top_windows(auto):
            try:        # the window may vanish / hang between listing and reading it
                out.append({**_describe_window(w), "foreground": w.NativeWindowHandle == fg_hwnd})
            except Exception:
                continue
        return out


def _find_window(auto: Any, spec: str) -> Any:
    s = spec.strip().lower()
    wins = _top_windows(auto)
    exact = [w for w in wins if w.Name.lower() == s]
    hits = exact or [w for w in wins if s in w.Name.lower()
                     or _proc_name(w.ProcessId).lower() in (s, f"{s}.exe")]
    if not hits:
        raise UIError(f"no window matches {spec!r}")
    if len(hits) > 1 and not exact:
        titles = ", ".join(repr(w.Name) for w in hits[:5])
        raise UIError(f"{len(hits)} windows match {spec!r}: {titles} — be more specific")
    return hits[0]


def _walk(root: Any, max_depth: int) -> Iterator[tuple[Any, int]]:
    stack = [(root, 0)]
    seen = 0
    while stack and seen < MAX_NODES:
        node, depth = stack.pop()
        seen += 1
        yield node, depth
        if depth < max_depth:
            try:
                children = node.GetChildren()
            except Exception:
                continue
            stack.extend((c, depth + 1) for c in reversed(children))


def _describe_control(c: Any) -> dict[str, Any]:
    return {"name": c.Name, "type": c.ControlTypeName, "automation_id": c.AutomationId,
            "enabled": bool(c.IsEnabled)}


def _find_control(window: Any, name: str | None, automation_id: str | None,
                  control_type: str | None, max_depth: int = 25) -> Any:
    if not (name or automation_id or control_type):
        raise UIError("give at least one of name, automation_id, control_type")
    exact, partial = [], []
    for c, _ in _walk(window, max_depth):
        if automation_id and c.AutomationId != automation_id:
            continue
        if control_type and c.ControlTypeName.lower() != control_type.lower():
            continue
        if name:
            n = (c.Name or "").lower()
            if n == name.lower():
                exact.append(c)
            elif name.lower() in n:
                partial.append(c)
        else:
            exact.append(c)
    hits = exact or partial
    if not hits:
        raise UIError(f"control not found (name={name!r}, id={automation_id!r}, "
                      f"type={control_type!r})")
    return hits[0]


def resolve(window: str, name: str | None = None, automation_id: str | None = None,
            control_type: str | None = None) -> dict[str, Any]:
    """What an action would target — used by the core to pick the permission level."""
    require_unlocked()
    with _uia() as auto:
        w = _find_window(auto, window)
        out: dict[str, Any] = {"window": _describe_window(w)}
        control = None
        if name or automation_id or control_type:
            control = _find_control(w, name, automation_id, control_type)
            out["control"] = _describe_control(control)
        out["window"]["is_shell"] = is_shell_window(
            out["window"]["process"], w.ClassName, w.Name,
            " ".join(filter(None, [control.Name, control.AutomationId])) if control else "")
        return out


def is_shell_window(process: str, klass: str, title: str, control_label: str = "") -> bool:
    """Would text/keys sent here be executed as a command? Fail closed."""
    proc, cls = process.lower(), (klass or "").lower()
    if cls in CONSOLE_CLASSES:
        return True
    if proc in SHELL_PROCESSES and not cls.startswith(GUI_CLASS_PREFIXES):
        return True
    if title.strip().lower() == "run" and cls == "#32770":        # Win+R dialog
        return True
    label = control_label.lower()
    return "terminal" in label or "console" in label             # e.g. VS Code terminal


def _focus_inside(auto: Any, w: Any) -> bool:
    focused = auto.GetFocusedControl()
    if focused is None:
        return False
    top = focused.GetTopLevelControl()
    return top is not None and top.NativeWindowHandle == w.NativeWindowHandle


def inspect(window: str, max_depth: int = 6, limit: int = 300) -> dict[str, Any]:
    require_unlocked()
    with _uia() as auto:
        w = _find_window(auto, window)
        nodes = []
        for c, depth in _walk(w, max_depth):
            if c.Name or c.AutomationId:
                nodes.append({**_describe_control(c), "depth": depth})
            if len(nodes) >= limit:
                break
        return {"window": _describe_window(w), "controls": nodes}


def _force_foreground(hwnd: int) -> None:
    """Windows blocks background processes from taking the foreground while the
    user works in another app. Attaching our input queue to the current
    foreground thread is the documented way round it — no fake keystrokes."""
    if sys.platform != "win32" or not hwnd:
        return
    import win32api
    import win32con
    import win32gui
    import win32process

    fg = win32gui.GetForegroundWindow()
    if fg == hwnd:
        return
    fg_thread = win32process.GetWindowThreadProcessId(fg)[0] if fg else 0
    me = win32api.GetCurrentThreadId()
    attached = bool(fg_thread) and fg_thread != me
    try:
        if attached:
            win32process.AttachThreadInput(me, fg_thread, True)
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
    except Exception:  # noqa: S110 - best effort; callers verify focus afterwards
        pass
    finally:
        if attached:
            with contextlib.suppress(Exception):
                win32process.AttachThreadInput(me, fg_thread, False)


def _activate(w: Any) -> None:
    try:
        w.SetActive()
    except Exception:
        w.SetFocus()
    time.sleep(0.15)
    _force_foreground(int(w.NativeWindowHandle or 0))
    time.sleep(0.15)


def focus(window: str) -> dict[str, Any]:
    require_unlocked()
    with _uia() as auto:
        w = _find_window(auto, window)
        _activate(w)
        fg = auto.GetForegroundControl()
        top = fg.GetTopLevelControl() if fg else None
        ok = top is not None and top.NativeWindowHandle == w.NativeWindowHandle
        return {"window": _describe_window(w), "foreground": ok, "verified": ok}


def _text_of(c: Any) -> str | None:
    for getter in ("GetValuePattern", "GetTextPattern"):
        try:
            pat = getattr(c, getter)()
            if getter == "GetValuePattern":
                return str(pat.Value)
            return str(pat.DocumentRange.GetText(-1))
        except Exception:
            continue
    # Static text (labels) exposes its content only through the UIA Name.
    if c.ControlTypeName == "TextControl":
        return str(c.Name)
    return None


def click(window: str, name: str | None, automation_id: str | None,
          control_type: str | None) -> dict[str, Any]:
    require_unlocked()
    with _uia() as auto:
        w = _find_window(auto, window)
        c = _find_control(w, name, automation_id, control_type)
        if not c.IsEnabled:
            raise UIError(f"control {c.Name!r} is disabled")
        used = None
        for pattern, method in (("GetInvokePattern", "Invoke"), ("GetTogglePattern", "Toggle"),
                                ("GetSelectionItemPattern", "Select"),
                                ("GetExpandCollapsePattern", "Expand")):
            try:
                getattr(getattr(c, pattern)(), method)()
                used = f"{pattern.removeprefix('Get')}.{method}"
                break
            except Exception:
                continue
        if used is None:
            raise UIError(f"{c.Name!r} supports no click pattern — keyboard or "
                          "coordinates would be needed")
        time.sleep(0.4)
        still = [x.NativeWindowHandle for x in _top_windows(auto)]
        return {"control": _describe_control(c), "method": used,
                "window_still_open": w.NativeWindowHandle in still,
                "foreground_title": (auto.GetForegroundControl().GetTopLevelControl().Name
                                     if auto.GetForegroundControl() else None)}


def type_text(window: str, text: str, name: str | None, automation_id: str | None,
              control_type: str | None, replace: bool = False) -> dict[str, Any]:
    """Insert text. By default existing content is KEPT: text is appended at the
    end. Only replace=True overwrites (the core gates that as ui.replace_text
    and backs up the old content first)."""
    require_unlocked()
    with _uia() as auto:
        w = _find_window(auto, window)
        c = _find_control(w, name, automation_id, control_type) if (
            name or automation_id or control_type) else auto.GetFocusedControl()
        before = _text_of(c)
        method = None
        if replace or not before:
            try:
                c.GetValuePattern().SetValue(text)
                method = "ValuePattern.SetValue"
            except Exception:
                method = None
        if method is None:
            _activate(w)
            c.SetFocus()
            time.sleep(0.1)
            if not _focus_inside(auto, w):
                # Never type blind: keys would land in whatever window is in front.
                raise UIError("could not move keyboard focus into the target window — "
                              "refusing to type")
            keys = ("{Ctrl}a" if replace else "{Ctrl}{End}") + _escape_sendkeys(text)
            auto.SendKeys(keys, interval=0.005, waitTime=0.1)
            method = "SendKeys(replace)" if replace else "SendKeys(append)"
        time.sleep(0.2)
        after = _text_of(c)
        kept = replace or not before or (after is not None and before in after)
        return {"control": _describe_control(c), "method": method, "before": before,
                "after": after, "previous_content_kept": kept,
                "verified": after is not None and text in after and kept}


def _escape_sendkeys(text: str) -> str:
    return "".join("{" + ch + "}" if ch in "{}" else ch for ch in text)


NAMED_KEYS = {"enter": "{Enter}", "tab": "{Tab}", "esc": "{Esc}", "escape": "{Esc}",
              "backspace": "{Back}", "delete": "{Delete}", "del": "{Delete}", "up": "{Up}",
              "down": "{Down}", "left": "{Left}", "right": "{Right}", "home": "{Home}",
              "end": "{End}", "pageup": "{PageUp}", "pagedown": "{PageDown}",
              "space": "{Space}", **{f"f{i}": f"{{F{i}}}" for i in range(1, 13)}}
MODIFIERS = {"ctrl": "{Ctrl}", "control": "{Ctrl}", "alt": "{Alt}", "shift": "{Shift}",
             "win": "{Win}"}


def to_sendkeys(combo: str) -> str:
    """'ctrl+shift+s' → '{Ctrl}{Shift}s'"""
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not parts:
        raise UIError("empty key combination")
    *mods, key = parts
    out = ""
    for m in mods:
        if m not in MODIFIERS:
            raise UIError(f"unknown modifier {m!r}")
        out += MODIFIERS[m]
    if key in NAMED_KEYS:
        return out + NAMED_KEYS[key]
    if len(key) == 1:
        return out + key
    raise UIError(f"unknown key {key!r}")


def send_keys(window: str, combo: str) -> dict[str, Any]:
    require_unlocked()
    keys = to_sendkeys(combo)
    with _uia() as auto:
        w = _find_window(auto, window)
        _activate(w)
        if not _focus_inside(auto, w):
            raise UIError("could not bring the target window to the front — refusing to "
                          "send keys")
        auto.SendKeys(keys, waitTime=0.2)
        fg = auto.GetForegroundControl()
        return {"keys": combo, "window_still_open": w.Exists(0, 0),
                "foreground_title": fg.GetTopLevelControl().Name if fg else None}


def read_value(window: str, name: str | None, automation_id: str | None,
               control_type: str | None) -> dict[str, Any]:
    require_unlocked()
    with _uia() as auto:
        w = _find_window(auto, window)
        c = _find_control(w, name, automation_id, control_type)
        return {"control": _describe_control(c), "value": _text_of(c)}


def click_xy(x: int, y: int) -> dict[str, Any]:
    """Last resort (plan §15, §17): a raw coordinate click."""
    require_unlocked()
    with _uia() as auto:
        auto.Click(x, y, waitTime=0.3)
        under = auto.ControlFromPoint(x, y)
        return {"x": x, "y": y, "under_cursor": _describe_control(under) if under else None}
