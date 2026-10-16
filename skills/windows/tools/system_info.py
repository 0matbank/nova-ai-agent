"""OS version, machine, user session, lock state, uptime, and every drive's size
and free space (Phase 16: the Windows Operator answers "C drive-e koto jayga"
without a shell command)."""

from __future__ import annotations

import getpass
import platform
import time

import psutil

from core.health import format_duration
from core.skills.api import Params, Tool, ToolEnv, ToolResult


class P(Params):
    pass


async def run(p: P, env: ToolEnv) -> ToolResult:
    data = {
        "os": platform.platform(), "machine": platform.machine(), "node": platform.node(),
        "user": getpass.getuser(), "cpu_count": psutil.cpu_count(),
        "boot_time": psutil.boot_time(), "uptime": format_duration(time.time() -
                                                                   psutil.boot_time()),
        "python": platform.python_version(),
        "drives": _drives(),
    }
    free = ", ".join(f"{d['drive']} {d['free_gb']} GB free of {d['total_gb']} GB"
                     for d in data["drives"])
    return ToolResult(True, f"{data['os']} · {data['node']} · up {data['uptime']}"
                            + (f" · {free}" if free else ""), data)


def _drives() -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for part in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(part.mountpoint)
        except OSError:                     # empty card reader, locked BitLocker drive …
            continue
        out.append({"drive": part.mountpoint.rstrip("\\/") or part.mountpoint,
                    "total_gb": round(u.total / 1024 ** 3, 1),
                    "free_gb": round(u.free / 1024 ** 3, 1), "used_percent": u.percent})
    return out


TOOL = Tool(name="system_info", params=P, run=run, action="pc.status", target=lambda p: "pc")
