"""Independent checks (plan §54): look at the PC itself after an agent changed it —
the file is really there / gone, the app really runs / stopped. Each check returns
(ok, proof) where the proof is a short line the owner sees."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import psutil

Resolve = Callable[[str], Path]


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


def check_effect(effect: dict[str, Any], resolve: Resolve) -> tuple[bool, str]:
    """Re-check one change an agent made (operator EFFECT_TOOLS)."""
    tool = f"{effect.get('skill')}.{effect.get('tool')}"
    p: dict[str, Any] = effect.get("params") or {}
    ev: dict[str, Any] = effect.get("evidence") or {}
    if tool in ("files.write", "files.mkdir"):
        path = resolve(str(p.get("path", "")))
        kind = path.is_dir() if tool == "files.mkdir" else path.is_file()
        return kind, f"{path.name} {'আছে ✓' if kind else 'নেই ✗'}"
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
        alive = pids_alive([*(ev.get("running_pids") or []), *(ev.get("new_pids") or [])])
        return bool(alive), (f"{p.get('app')} চলছে (pid {alive[0]}) ✓" if alive
                             else f"{p.get('app')} চলছে না ✗")
    if tool == "app-control.close":
        still = pids_alive(ev.get("still_running") or [])
        return not still, (f"{p.get('app')} বন্ধ ✓" if not still
                           else f"{p.get('app')} এখনো চলছে (pid {still}) ✗")
    out = ev.get("output")
    if out:
        # Phase 20 media / documents / archive / image: the new file is really there
        path = Path(str(out))
        return path.exists(), f"{path.name} {'আছে ✓' if path.exists() else 'নেই ✗'}"
    return True, f"{tool} (tool-confirmed)"
