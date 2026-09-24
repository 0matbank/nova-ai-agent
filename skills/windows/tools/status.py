"""CPU / GPU / RAM / disk status (plan §42)."""

from __future__ import annotations

from dataclasses import asdict

from core.health import format_pc_status, pc_status
from core.skills.api import Params, Tool, ToolEnv, ToolResult


class P(Params):
    pass


async def run(p: P, env: ToolEnv) -> ToolResult:
    s = await pc_status(env.config.root)
    data = asdict(s)
    return ToolResult(True, format_pc_status(s), data,
                      {"ram_percent": s.ram_percent, "gpus": len(s.gpus)})


TOOL = Tool(name="status", params=P, run=run, action="pc.status", target=lambda p: "pc")
