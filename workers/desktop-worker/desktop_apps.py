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
# V1.1: the PC's installed apps, scanned by the core (data/app_catalog.json). Only an
# app listed there can be opened, and only by its id — the core never sends a path.
CATALOG_FILE: Path | None = None


class AppError(Exception):
    pass


def load_apps() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(APPS_FILE.read_text(encoding="utf-8"))
    return data


def catalog_app(app_id: str) -> dict[str, Any] | None:
    """An installed app from the catalog, in the shape the open/close code uses."""
    if CATALOG_FILE is None:
        return None
    try:
        data = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for a in data.get("apps", []):
        if a.get("id") == app_id and a.get("installed", True) and a.get("kind") in (
                "lnk", "uwp", "exe"):
            return {"kind": a["kind"], "launch": a["launch"], "name": a["name"],
                    "process": list(a.get("process") or [])}
    return None


def canonical(name: str) -> tuple[str, dict[str, Any]]:
    data = load_apps()
    key = name.strip().lower()
    key = data["aliases"].get(key, key)
    app = data["apps"].get(key) or catalog_app(name.strip())
    if app is None:
        raise AppError(f"unknown app {name!r} (not in the installed app list)")
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


def _launch(app: dict[str, Any], args: list[str]) -> str:
    kind = app.get("kind")
    if kind == "lnk":
        # the Start Menu shortcut knows the right exe, arguments and folder
        if args:
            raise AppError("arguments are not supported for this app")
        os.startfile(app["launch"])  # type: ignore[attr-defined]  # noqa: S606
        return str(app["launch"])
    if kind == "uwp":
        if args:
            raise AppError("arguments are not supported for this app")
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app['launch']}"],  # noqa: S603,S607
                         close_fds=True)
        return f"shell:AppsFolder\\{app['launch']}"
    exe = app["launch"] if kind == "exe" else resolve_exe(app)
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    subprocess.Popen([exe, *args], creationflags=flags, close_fds=True,  # noqa: S603
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
    return str(exe)


def _titled(name: str) -> list[int]:
    """Visible windows whose title names the app (Store apps: no known exe)."""
    if sys.platform != "win32":
        return []
    import win32gui

    word = name.split()[0].lower() if name.split() else name.lower()
    found: list[int] = []

    def cb(hwnd: int, _: Any) -> bool:
        if win32gui.IsWindowVisible(hwnd) and word in win32gui.GetWindowText(hwnd).lower():
            found.append(hwnd)
        return True

    win32gui.EnumWindows(cb, None)
    return found


def open_app(name: str, args: list[str], wait_seconds: float = 10.0) -> dict[str, Any]:
    key, app = canonical(name)
    if not app["process"]:
        # no exe to watch (Store app / shell shortcut): a new window with its name
        before_w = set(_titled(app.get("name", key)))
        exe = _launch(app, args)
        deadline = time.monotonic() + wait_seconds
        new_w: list[int] = []
        while time.monotonic() < deadline and not new_w:
            new_w = [h for h in _titled(app.get("name", key)) if h not in before_w]
            time.sleep(0.25)
        return {"app": key, "exe": exe, "new_pids": [], "running_pids": [],
                "visible_windows": len(new_w), "verified": bool(new_w),
                "window_titled": bool(new_w)}
    before = {p.pid for p in running(app["process"])}
    exe = _launch(app, args)
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
    # V1.1: big apps (Premiere, After Effects) show their window long after starting —
    # a NEW process of the app counts as opened (else a retry would start it twice)
    started = bool(set(now_pids) - before)
    return {"app": key, "exe": exe, "new_pids": new, "running_pids": now_pids,
            "visible_windows": len(windows), "verified": bool(now_pids and (windows or started)),
            "still_starting": bool(started and not windows)}


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
    if not app["process"]:
        raise AppError(f"{key}: Nova does not know which program to close for this app")
    if app.get("no_close"):
        raise AppError(f"{key} is the Windows shell — closing it is not allowed")
    procs = running(app["process"])
    if not procs:
        return {"app": key, "was_running": False, "windows_signalled": 0,
                "still_running": [], "verified": True, "process": list(app["process"])}
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
            "still_running": [p.pid for p in left], "verified": not left,
            "process": list(app["process"])}


def kill_app(name: str) -> dict[str, Any]:
    key, app = canonical(name)
    if not app["process"]:
        raise AppError(f"{key}: Nova does not know which program to stop for this app")
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
