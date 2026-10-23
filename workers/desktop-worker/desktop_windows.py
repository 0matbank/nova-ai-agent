"""What the owner sees open (V1.1): top-level visible windows of the interactive desktop,
grouped by app — not the process list. Background-only programs (llama-server.exe,
MsMpEng.exe …) have no visible window, so they never show up here.

Win32 only (EnumWindows): visible, titled, not a tool/owned popup, not cloaked (a
hidden Store app or another virtual desktop), not the shell itself.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import psutil

# set by the service from the config (data/app_catalog.json, written by the core)
CATALOG_FILE: Path | None = None

# the desktop itself, taskbar, start menu, input panels — not apps the owner opened
SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
                 "Windows.UI.Core.CoreWindow", "NotifyIconOverflowWindow",
                 "TopLevelWindowForOverflowXamlIsland", "XamlExplorerHostIslandWindow"}
SHELL_TITLES = {"Program Manager", "Windows Input Experience", "Settings", ""}
UWP_HOST = "applicationframehost.exe"
FRIENDLY = {"explorer.exe": "File Explorer", "chrome.exe": "Google Chrome",
            "msedge.exe": "Microsoft Edge", "code.exe": "Visual Studio Code",
            "windowsterminal.exe": "Terminal", "cmd.exe": "Command Prompt",
            "powershell.exe": "Windows PowerShell", "notepad.exe": "Notepad",
            "taskmgr.exe": "Task Manager"}
GWL_EXSTYLE, WS_EX_TOOLWINDOW, GW_OWNER, DWMWA_CLOAKED = -20, 0x80, 4, 14


def _cloaked(hwnd: int) -> bool:
    val = ctypes.c_int(0)
    with contextlib.suppress(Exception):
        ctypes.windll.dwmapi.DwmGetWindowAttribute(  # type: ignore[attr-defined]
            ctypes.c_void_p(hwnd), DWMWA_CLOAKED, ctypes.byref(val), ctypes.sizeof(val))
    return val.value != 0


def _catalog_names() -> dict[str, str]:
    """exe file name → the app's display name, from the app catalog."""
    if CATALOG_FILE is None:
        return {}
    try:
        data = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, str] = {}
    for a in data.get("apps", []):
        for exe in a.get("process") or []:
            out.setdefault(str(exe).lower(), str(a.get("name") or exe))
    return out


@lru_cache(maxsize=256)
def _file_description(exe_path: str) -> str:
    if sys.platform != "win32" or not exe_path:
        return ""
    import win32api
    with contextlib.suppress(Exception):
        lang, cp = win32api.GetFileVersionInfo(exe_path, "\\VarFileInfo\\Translation")[0]
        desc = win32api.GetFileVersionInfo(
            exe_path, f"\\StringFileInfo\\{lang:04x}{cp:04x}\\FileDescription")
        return str(desc or "").strip()
    return ""


def friendly_name(exe: str, exe_path: str, title: str, names: dict[str, str]) -> str:
    low = exe.lower()
    if low == UWP_HOST:                    # Store apps are hosted: the title names them
        return title.split(" - ")[-1].strip() or title
    return (names.get(low) or FRIENDLY.get(low) or _file_description(exe_path)
            or Path(exe).stem or exe)


def _raw_windows() -> list[tuple[int, str, str, int, bool]]:
    """(hwnd, title, class, pid, minimized) of the candidate windows."""
    import win32gui
    import win32process

    found: list[tuple[int, str, str, int, bool]] = []

    def keep(hwnd: int) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd).strip()
        cls = win32gui.GetClassName(hwnd)
        if title in SHELL_TITLES or cls in SHELL_CLASSES:
            return
        if win32gui.GetWindowLong(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return
        if win32gui.GetWindow(hwnd, GW_OWNER):
            return                          # dialogs/popups belong to their app window
        if _cloaked(hwnd):
            return
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        found.append((hwnd, title, cls, pid, bool(win32gui.IsIconic(hwnd))))

    def cb(hwnd: int, _: Any) -> bool:
        with contextlib.suppress(Exception):     # a window can vanish while being read
            keep(hwnd)
        return True

    win32gui.EnumWindows(cb, None)
    return found


def visible_apps() -> dict[str, Any]:
    if sys.platform != "win32":
        return {"apps": [], "windows": 0}
    import win32gui
    fg = win32gui.GetForegroundWindow()
    names = _catalog_names()
    apps: dict[str, dict[str, Any]] = {}
    count = 0
    for hwnd, title, _cls, pid, minimized in _raw_windows():
        try:
            proc = psutil.Process(pid)
            exe, exe_path = proc.name(), proc.exe()
        except psutil.Error:
            exe, exe_path = "", ""
        name = friendly_name(exe, exe_path, title, names)
        a = apps.setdefault(name.lower(), {"name": name, "process": exe, "windows": [],
                                           "foreground": False})
        a["windows"].append({"title": title, "minimized": minimized})
        a["foreground"] = a["foreground"] or hwnd == fg
        count += 1
    ordered = sorted(apps.values(), key=lambda a: (not a["foreground"], a["name"].lower()))
    return {"apps": ordered, "windows": count}
