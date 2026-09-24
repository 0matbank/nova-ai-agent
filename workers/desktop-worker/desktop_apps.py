"""App open/close inside the user session (Desktop Worker, plan §4, §17, §54).
Native API first (plan §17): process launch + WM_CLOSE, verified via the
process table — never mouse coordinates."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import psutil

APPS_FILE = Path(__file__).resolve().parents[2] / "skills" / "app-control" / "apps.json"


class AppError(Exception):
    pass


def load_apps() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(APPS_FILE.read_text(encoding="utf-8"))
    return data


def canonical(name: str) -> tuple[str, dict[str, Any]]:
    data = load_apps()
    key = name.strip().lower()
    key = data["aliases"].get(key, key)
    app = data["apps"].get(key)
    if app is None:
        raise AppError(f"unknown app {name!r} (known: {', '.join(sorted(data['apps']))})")
    return key, app


def _app_paths_registry(exe: str) -> str | None:
    if sys.platform != "win32":
        return None
    import winreg
    sub = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, sub) as k:
                value, _ = winreg.QueryValueEx(k, "")
                if value and Path(str(value).strip('"')).exists():
                    return str(value).strip('"')
        except OSError:
            continue
    return None


def resolve_exe(app: dict[str, Any]) -> str:
    for cand in app["exe"]:
        expanded = os.path.expandvars(cand)
        if os.path.isabs(expanded) and Path(expanded).exists():
            return expanded
        found = shutil.which(expanded) or _app_paths_registry(expanded)
        if found:
            return found
    raise AppError(f"{app['exe'][0]} is not installed or not found")


def running(process_names: list[str]) -> list[psutil.Process]:
    wanted = {n.lower() for n in process_names}
    out = []
    for p in psutil.process_iter(["name"]):
        if (p.info["name"] or "").lower() in wanted:
            out.append(p)
    return out


def open_app(name: str, args: list[str], wait_seconds: float = 10.0) -> dict[str, Any]:
    key, app = canonical(name)
    exe = resolve_exe(app)
    before = {p.pid for p in running(app["process"])}
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    subprocess.Popen([exe, *args], creationflags=flags, close_fds=True,  # noqa: S603
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
    # Verified = the app's process exists AND it shows a visible window
    # (a process alone may still be starting up, plan §54).
    deadline = time.monotonic() + wait_seconds
    new: list[int] = []
    windows: list[int] = []
    while time.monotonic() < deadline:
        now = running(app["process"])
        new = [p.pid for p in now if p.pid not in before]
        windows = visible_windows({p.pid for p in now})
        if windows and (new or before):
            break
        time.sleep(0.25)
    now_pids = [p.pid for p in running(app["process"])]
    return {"app": key, "exe": exe, "new_pids": new, "running_pids": now_pids,
            "visible_windows": len(windows), "verified": bool(now_pids and windows)}


def visible_windows(pids: set[int]) -> list[int]:
    if sys.platform != "win32" or not pids:
        return []
    import win32gui
    import win32process

    found: list[int] = []

    def cb(hwnd: int, _: Any) -> bool:
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd):
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid in pids:
                found.append(hwnd)
        return True

    win32gui.EnumWindows(cb, None)
    return found


def _signal_close(hwnd: int) -> None:
    if sys.platform != "win32":
        return
    import win32con
    import win32gui

    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)


def close_app(name: str, wait_seconds: float = 8.0) -> dict[str, Any]:
    key, app = canonical(name)
    if app.get("no_close"):
        raise AppError(f"{key} is the Windows shell — closing it is not allowed")
    procs = running(app["process"])
    if not procs:
        return {"app": key, "was_running": False, "windows_signalled": 0,
                "still_running": [], "verified": True}
    deadline = time.monotonic() + wait_seconds
    sent = 0
    left = procs
    signalled: set[int] = set()
    while time.monotonic() < deadline:
        left = running(app["process"])
        if not left:
            break
        # Windows may still be appearing (just-launched app): signal each once.
        for hwnd in visible_windows({p.pid for p in left}):
            if hwnd not in signalled:
                _signal_close(hwnd)
                signalled.add(hwnd)
                sent += 1
        time.sleep(0.25)
    return {"app": key, "was_running": True, "windows_signalled": sent,
            "still_running": [p.pid for p in left], "verified": not left}


def kill_app(name: str) -> dict[str, Any]:
    key, app = canonical(name)
    if app.get("no_close"):
        raise AppError(f"{key} is the Windows shell — killing it is not allowed")
    procs = running(app["process"])
    for p in procs:
        with contextlib.suppress(psutil.Error):
            p.kill()
    psutil.wait_procs(procs, timeout=5)
    left = running(app["process"])
    return {"app": key, "killed": [p.pid for p in procs], "still_running": [p.pid for p in left],
            "verified": not left}
