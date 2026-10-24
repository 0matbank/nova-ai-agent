"""Independent checks (plan §54): look at the PC itself after an agent changed it —
the file is really there / gone, the app really runs / stopped. Each check returns
(ok, proof) where the proof is a short line the owner sees.

ok is True (confirmed), False (the PC says otherwise) or None — the result could not be
checked (CU-2, plan §72): never a PASS, never a silent "tool-confirmed"."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import psutil

Resolve = Callable[[str], Path]
Check = tuple[bool | None, str]
MAX_COMPARE_BYTES = 20 * 2**20          # larger files: compare only the expected tail


def _written(path: Path, content: str, mode: str, encoding: str) -> Check:
    """files.write: the file holds exactly what was asked (append: ends with it)."""
    if not path.is_file():
        return False, f"{path.name} নেই ✗"
    try:
        want = content.encode(encoding or "utf-8")
    except LookupError:
        return None, f"{path.name}: encoding {encoding!r} চেনা যায়নি — content যাচাই হয়নি"
    size = path.stat().st_size
    if mode == "append":
        if size < len(want):
            return False, f"{path.name}-এর শেষে লেখাটা নেই ✗"
        with path.open("rb") as fh:
            fh.seek(size - len(want))
            ok = fh.read() == want
    else:
        if size != len(want):
            return False, f"{path.name}: আকার মেলেনি ({size} ≠ {len(want)} bytes) ✗"
        if size > MAX_COMPARE_BYTES:
            return None, f"{path.name}: আকার মিলেছে, content অনেক বড় — পুরো তুলনা হয়নি"
        ok = path.read_bytes() == want
    return ok, (f"{path.name}-এ চাওয়া লেখাই আছে ✓" if ok else
                f"{path.name}-এর লেখা চাওয়া লেখার সঙ্গে মেলেনি ✗")


def visible_window_titles() -> list[tuple[int, str]] | None:
    """(pid, title) of visible titled windows; None where this cannot be checked."""
    import sys
    if sys.platform != "win32":
        return None
    import win32gui
    import win32process
    out: list[tuple[int, str]] = []

    def cb(hwnd: int, _: Any) -> bool:
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title:
                out.append((win32process.GetWindowThreadProcessId(hwnd)[1], title))
        return True

    win32gui.EnumWindows(cb, None)
    return out


def _app_word(app: str) -> str:
    return app.replace("-", " ").split()[0].lower() if app.strip() else ""


def _process_running(names: Iterable[str]) -> list[int]:
    wanted = {n.lower() for n in names}
    return [p.pid for p in psutil.process_iter(["name"])
            if (p.info["name"] or "").lower() in wanted]


def pids_alive(pids: Iterable[Any]) -> list[int]:
    alive = []
    for pid in pids or ():
        try:
            if psutil.pid_exists(int(pid)) and psutil.Process(int(pid)).status() != \
                    psutil.STATUS_ZOMBIE:
                alive.append(int(pid))
        except (psutil.Error, ValueError, TypeError):
            continue
    return alive


def check_effect(effect: dict[str, Any], resolve: Resolve) -> Check:
    """Re-check one change an agent made (operator EFFECT_TOOLS)."""
    tool = f"{effect.get('skill')}.{effect.get('tool')}"
    p: dict[str, Any] = effect.get("params") or {}
    ev: dict[str, Any] = effect.get("evidence") or {}
    if tool == "files.write":
        return _written(resolve(str(p.get("path", ""))), str(p.get("content", "")),
                        str(p.get("mode", "create")), str(p.get("encoding", "utf-8")))
    if tool == "files.mkdir":
        path = resolve(str(p.get("path", "")))
        return path.is_dir(), f"{path.name} {'আছে ✓' if path.is_dir() else 'নেই ✗'}"
    if tool == "files.copy":
        dst = resolve(str(p.get("destination", "")))
        return dst.exists(), f"কপি {dst.name} {'আছে ✓' if dst.exists() else 'নেই ✗'}"
    if tool == "files.move":
        src, dst = resolve(str(p.get("source", ""))), resolve(str(p.get("destination", "")))
        ok = dst.exists() and not src.exists()
        return ok, (f"{dst.name} নতুন জায়গায় ✓" if ok else
                    f"move মেলেনি ✗ (নতুন জায়গায় {'আছে' if dst.exists() else 'নেই'}, "
                    f"পুরোনো জায়গায় {'এখনো আছে' if src.exists() else 'নেই'})")
    if tool == "files.delete":
        path = resolve(str(p.get("path", "")))
        return not path.exists(), (f"{path.name} মুছে গেছে (Recycle Bin) ✓" if not path.exists()
                                   else f"{path.name} এখনো আছে ✗")
    if tool == "app-control.open":
        app = str(p.get("app", ""))
        windows = visible_window_titles()
        if ev.get("window_titled") and not ev.get("running_pids"):
            # a Store app has no exe to watch: look for its window ourselves now
            if windows is None:
                return None, f"{app}: window নিজে দেখা যায়নি — যাচাই হয়নি"
            word = _app_word(app)
            seen = [t for _, t in windows if word and word in t.lower()]
            return bool(seen), (f"{app}-এর window খোলা: «{seen[0][:40]}» ✓" if seen
                                else f"{app}-এর কোনো window দেখা যাচ্ছে না ✗")
        alive = pids_alive([*(ev.get("running_pids") or []), *(ev.get("new_pids") or [])])
        if not alive:
            return False, f"{app} চলছে না ✗"
        if windows is not None and not any(pid in alive for pid, _ in windows):
            if ev.get("still_starting"):
                return None, f"{app} চালু হচ্ছে (pid {alive[0]}), window এখনো আসেনি"
            return None, f"{app} চলছে (pid {alive[0]}) কিন্তু তার কোনো window দেখা যাচ্ছে না"
        return True, f"{app} চলছে, window খোলা (pid {alive[0]}) ✓"
    if tool == "app-control.close":
        app = str(p.get("app", ""))
        names = ev.get("process") or []
        # look again ourselves — an empty "still running" list proves nothing
        still = _process_running(names) if names else pids_alive(ev.get("still_running") or [])
        if not names and not ev.get("still_running") and ev.get("was_running") is not False:
            return None, f"{app}: কোন program বন্ধ হলো জানা নেই — যাচাই হয়নি"
        return not still, (f"{app} বন্ধ ✓" if not still
                           else f"{app} এখনো চলছে (pid {still}) ✗")
    if tool == "desktop-ui.type":
        after, typed = str(ev.get("after", "")), str(p.get("text", ""))
        ok = bool(typed) and typed in after
        return ok, ("লেখা বসেছে (পড়ে মিলিয়ে দেখা) ✓" if ok
                    else "যা লেখার কথা ছিল, পড়ে তা পাওয়া যায়নি ✗")
    if tool in ("desktop-ui.click", "desktop-ui.click_xy", "desktop-ui.keys"):
        # a click has no result of its own to read back; the goal check decides
        return None, f"{tool}: কাজ পাঠানো হয়েছে, ফল আলাদা করে যাচাই হয়নি"
    if tool.startswith("admin."):
        return check_admin(tool, p, ev)
    out = ev.get("output")
    if out:
        # Phase 20 media / documents / archive / image: the new file is really there
        path = Path(str(out))
        return path.exists(), f"{path.name} {'আছে ✓' if path.exists() else 'নেই ✗'}"
    # CU-2: no check exists for this effect → unverified, never a silent PASS
    return None, f"{tool}: যাচাইয়ের উপায় নেই"


# ------------------------------------------------------------ Phase 22: admin work
# Checked from Nova's own normal (non-elevated) session — not the broker's word.

def _system32() -> Path:
    import os
    return Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32"


def _service_state(name: str) -> str | None:
    try:
        get = getattr(psutil, "win_service_get")      # Windows-only psutil API  # noqa: B009
        return str(get(name).status())
    except (AttributeError, psutil.Error, OSError):
        return None


def _rule_exists(name: str) -> bool | None:
    import subprocess
    try:
        r = subprocess.run([str(_system32() / "netsh.exe"), "advfirewall", "firewall",  # noqa: S603
                            "show", "rule", f"name={name}"], capture_output=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                           check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.returncode == 0 and name.encode() in r.stdout


def check_admin(tool: str, p: dict[str, Any], ev: dict[str, Any]) -> Check:
    op = p.get("op")
    if tool == "admin.service" and op in ("start", "stop", "restart"):
        want = "stopped" if op == "stop" else "running"
        state = _service_state(str(p.get("name")))
        if state is None:
            return None, f"{p.get('name')}: নিজে দেখা যায়নি (broker বলছে {ev.get('state')})"
        return state == want, f"{p.get('name')} service {state} {'✓' if state == want else '✗'}"
    if tool == "admin.firewall" and op in ("add", "remove"):
        rule = f"Nova AI - {p.get('name')}"
        exists = _rule_exists(rule)
        if exists is None:
            return None, f"'{rule}': নিজে দেখা যায়নি (broker-এর কথা)"
        ok = exists == (op == "add")
        return ok, f"firewall rule '{rule}' {'আছে' if exists else 'নেই'} {'✓' if ok else '✗'}"
    if tool == "admin.package":
        ok = ev.get("installed") is (op == "install")
        return ok, f"{p.get('id')} {'installed' if ev.get('installed') else 'not installed'} " \
                   f"(winget list) {'✓' if ok else '✗'}"
    if tool == "admin.power" and op in ("restart", "shutdown"):
        return bool(ev.get("scheduled")), (f"{op} {ev.get('delay_seconds')} s পরে scheduled "
                                           "(cancel করা যায়) ✓")
    if tool == "admin.task":
        ok = ev.get("exit_code") == 0
        return ok, f"{p.get('name')} exit {ev.get('exit_code')} {'✓' if ok else '✗'}"
    return True, f"{tool} (read-only)"
