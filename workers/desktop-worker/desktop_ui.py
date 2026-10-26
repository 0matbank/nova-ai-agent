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
# Windows whose text boxes run what is typed (audit 2026-10-15): Explorer's address bar
# and the Start/taskbar search (explorer.exe), Task Manager's "Create new task".
COMMAND_HOSTS = {"explorer.exe", "taskmgr.exe"}
RUN_DIALOG_TITLES = {"run", "create new task"}
# Editors with an integrated terminal: when the target control is not known, the focused
# control may be that terminal — fail closed.
IDE_PROCESSES = {"code.exe", "cursor.exe", "windsurf.exe", "antigravity ide.exe",
                 "devenv.exe", "pycharm64.exe", "idea64.exe", "rider64.exe"}
CONSOLE_CLASSES = {"consolewindowclass", "cascadia_hosting_window_class",
                   "pseudoconsolewindow", "mintty", "virtualconsoleclass"}
# GUI framework windows hosted by a shell process (e.g. a WinForms form run
# from PowerShell) take no commands — they are not command sinks.
GUI_CLASS_PREFIXES = ("windowsforms10.", "hwndwrapper[",
                      "#32770")   # a standard dialog box (MessageBox): buttons, no prompt
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


def win32_class(w: Any) -> str:
    """The real Windows class name (e.g. "HwndWrapper[…]" for WPF, "ConsoleWindowClass").
    UI Automation's ClassName can be a framework name ("Window" for WPF) — the shell
    checks need the Win32 one (found 2026-10-24: a WPF window counted as a terminal)."""
    try:
        import win32gui
        return str(win32gui.GetClassName(int(w.NativeWindowHandle)))
    except Exception:  # noqa: BLE001 - no handle: fall back to what UIA says
        return str(w.ClassName or "")


def front_window(auto: Any) -> Any:
    """The top-level window in front, or None while Windows cannot say (one closing,
    hung or changing right now) — never an exception for that."""
    try:
        fg = auto.GetForegroundControl()
        return fg.GetTopLevelControl() if fg else None
    except Exception:  # noqa: BLE001 - COMError while the front window changes
        return None


def _front_title(auto: Any) -> str | None:
    top = front_window(auto)
    return str(top.Name) if top is not None else None


def _describe_window(w: Any) -> dict[str, Any]:
    return {"title": w.Name, "hwnd": w.NativeWindowHandle, "pid": w.ProcessId,
            "process": _proc_name(w.ProcessId), "class": win32_class(w)}


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
        top = front_window(auto)
        fg_hwnd = top.NativeWindowHandle if top else None
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
    try:
        secret = bool(c.IsPassword)
    except Exception:  # noqa: BLE001 - a control without the property is not a password
        secret = False
    return {"name": c.Name, "type": c.ControlTypeName, "automation_id": c.AutomationId,
            "enabled": bool(c.IsEnabled), "password": secret}


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
        elif _focus_inside(auto, w):
            # no control named: text/keys go to the focused control — judge that one
            control = auto.GetFocusedControl()
        out["window"]["is_shell"] = is_shell_window(
            out["window"]["process"], win32_class(w), w.Name,
            " ".join(filter(None, [control.Name, control.AutomationId,
                                   getattr(control, "ClassName", "")])) if control else "",
            control_known=control is not None)
        return out


def is_shell_window(process: str, klass: str, title: str, control_label: str = "",
                    control_known: bool = True) -> bool:
    """Would text/keys sent here be executed as a command? Fail closed."""
    proc, cls = process.lower(), (klass or "").lower()
    if cls in CONSOLE_CLASSES:
        return True
    if proc in SHELL_PROCESSES and not cls.startswith(GUI_CLASS_PREFIXES):
        return True
    if proc in COMMAND_HOSTS:                     # address bar / Start search / new task
        return True
    if title.strip().lower() in RUN_DIALOG_TITLES and cls == "#32770":   # Win+R, new task
        return True
    label = control_label.lower()
    if "terminal" in label or "console" in label or "xterm" in label:   # e.g. VS Code terminal
        return True
    return proc in IDE_PROCESSES and not control_known


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
    if win32gui.GetForegroundWindow() != hwnd:
        # CU-3b: a background worker may set the foreground only if it sent the last
        # input. A zero-distance mouse move (nothing moves, nothing is clicked) does
        # that — never while the owner is using the PC.
        import desktop_guard
        if not desktop_guard.owner_active():
            desktop_guard.nudge_input()
            with contextlib.suppress(Exception):
                win32gui.SetForegroundWindow(hwnd)
            if win32gui.GetForegroundWindow() != hwnd:
                # last resort (2026-10-24: a hidden GameInputSvc window held the
                # foreground and every other way failed): minimize + restore the TARGET
                # window — Windows activates a window it restores
                with contextlib.suppress(Exception):
                    win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)


def _activate(w: Any) -> None:
    import desktop_guard
    desktop_guard.require_owner_idle()       # never take the front while the owner works
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
        top = front_window(auto)
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
                "foreground_title": _front_title(auto)}


def type_text(window: str, text: str, name: str | None, automation_id: str | None,
              control_type: str | None, replace: bool = False) -> dict[str, Any]:
    """Insert text. By default existing content is KEPT: text is appended at the
    end. Only replace=True overwrites (the core gates that as ui.replace_text
    and backs up the old content first)."""
    require_unlocked()
    with _uia() as auto:
        w = _find_window(auto, window)
        if name or automation_id or control_type:
            c = _find_control(w, name, automation_id, control_type)
        else:
            # no control named: use the focused one, but only if it belongs to THIS
            # window — never write into another app's field (audit 2026-10-15)
            _activate(w)
            time.sleep(0.1)
            if not _focus_inside(auto, w):
                raise UIError("the target window does not have keyboard focus — name the "
                              "control to type into")
            c = auto.GetFocusedControl()
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
            import desktop_guard
            desktop_guard.note_injected()
            method = "SendKeys(replace)" if replace else "SendKeys(append)"
        time.sleep(0.2)
        after = _text_of(c)
        kept = replace or not before or (after is not None and before in after)
        return {"control": _describe_control(c), "method": method, "before": before,
                "after": after, "previous_content_kept": kept,
                "verified": after is not None and text in after and kept}


# V1.2 dictation: never type into something that is not a text field — in a list or a
# tree, letters jump between items; on a button, Space presses it
NOT_TEXT = frozenset({
    "ButtonControl", "SplitButtonControl", "ListControl", "ListItemControl", "TreeControl",
    "TreeItemControl", "MenuItemControl", "MenuBarControl", "MenuControl", "TabControl",
    "TabItemControl", "CheckBoxControl", "RadioButtonControl", "HyperlinkControl",
    "WindowControl", "TitleBarControl", "ToolBarControl", "ImageControl", "DataItemControl",
    "TableControl", "HeaderItemControl", "ScrollBarControl", "SliderControl"})


def _focused_field(auto: Any) -> tuple[Any, Any]:
    w = front_window(auto)
    if w is None:
        raise UIError("no window is in front right now")
    c = auto.GetFocusedControl()
    if c is None or not _focus_inside(auto, w):
        raise UIError("nothing in the window in front has the keyboard focus")
    return w, c


def _read_only(c: Any) -> bool:
    try:
        return bool(c.GetValuePattern().IsReadOnly)
    except Exception:  # noqa: BLE001 - no ValuePattern: the control type decides
        return False


def _takes_text(c: Any) -> bool:
    # only a plain edit box is trusted to say "read-only": rich editors (Word, browsers,
    # Notepad's document) report a read-only ValuePattern and still take typing
    # (owner 2026-10-26: dictation refused a DocumentControl)
    if c.ControlTypeName == "EditControl" and _read_only(c):
        return False
    return c.ControlTypeName not in NOT_TEXT


def focused() -> dict[str, Any]:
    """Where typed text would land now: the front window and its focused control."""
    require_unlocked()
    with _uia() as auto:
        w, c = _focused_field(auto)
        return {"window": _describe_window(w), "control": _describe_control(c),
                "takes_text": _takes_text(c)}


def dictate(text: str, window: str | None = None) -> dict[str, Any]:
    """Type at the cursor of the focused field in the window in front — the owner put
    the cursor there (V1.2 dictation). Never into a password field or a non-text
    control; never somewhere else when focus is gone. `window`: only if the window in
    front is this one (exact title) — else nothing is typed."""
    require_unlocked()
    with _uia() as auto:
        w, c = _focused_field(auto)
        if window is not None and str(w.Name) != window:
            raise UIError(f"the window in front is «{w.Name}», not «{window}» — nothing typed")
        info = _describe_control(c)
        if info["password"]:
            raise UIError("the focused field is a password field — never typed by voice")
        if not _takes_text(c):
            raise UIError(f"the focused control ({info['type']}) does not take text — click "
                          "into a text field first")
        before = _text_of(c)
        keys = "{Enter}".join(_escape_sendkeys(line) for line in text.split("\n"))
        auto.SendKeys(keys, interval=0.005, waitTime=0.1)
        import desktop_guard
        desktop_guard.note_injected()
        time.sleep(0.2)
        after = _text_of(c)
        lines = [ln for ln in text.split("\n") if ln.strip()]
        landed = after is not None and all(ln in after for ln in lines)
        grew = after is not None and before is not None and len(after) > len(before)
        return {"window": _describe_window(w), "control": info,
                "method": "SendKeys(at the cursor)", "readable": after is not None,
                "verified": landed and (grew or not before)}


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
        import desktop_guard
        desktop_guard.note_injected()
        return {"keys": combo, "window_still_open": w.Exists(0, 0),
                "foreground_title": _front_title(auto)}


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
        import desktop_guard
        desktop_guard.note_injected()
        under = auto.ControlFromPoint(x, y)
        return {"x": x, "y": y, "under_cursor": _describe_control(under) if under else None}
