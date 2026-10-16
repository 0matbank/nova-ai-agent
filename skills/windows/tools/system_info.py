"""OS version, machine, user session, lock state, uptime, and every drive's size
and free space (Phase 16: the Windows Operator answers "C drive-e koto jayga"
without a shell command)."""

from __future__ import annotations

import getpass
import platform
import time

import psutil

from core.health import drive_usage, format_drives, format_duration
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
        "drives": drive_usage(),
    }
    free = format_drives(data["drives"])
    return ToolResult(True, f"{data['os']} · {data['node']} · up {data['uptime']}"
                            + (f" · {free}" if free else ""), data)


TOOL = Tool(name="system_info", params=P, run=run, action="pc.status", target=lambda p: "pc")
