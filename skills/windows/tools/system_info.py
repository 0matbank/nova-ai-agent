"""OS version, machine, user session, lock state, uptime."""

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
    }
    return ToolResult(True, f"{data['os']} · {data['node']} · up {data['uptime']}", data)


TOOL = Tool(name="system_info", params=P, run=run, action="pc.status", target=lambda p: "pc")
