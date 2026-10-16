"""A Windows service: query (GREEN) or start/stop/restart an allowlisted one (RED)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from core.broker.skill import run_on_broker
from core.skills.api import Params, Tool, ToolEnv, ToolResult


class P(Params):
    name: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,80}$",
                      description="service name, e.g. Spooler (not the display name)")
    op: Literal["query", "start", "stop", "restart"] = "query"


def action(p: P, env: ToolEnv) -> str:
    return "admin.status" if p.op == "query" else "admin.service_control"


async def run(p: P, env: ToolEnv) -> ToolResult:
    return await run_on_broker("service", p, f"service {p.name}", env)


TOOL = Tool(name="service", params=P, run=run, action=action,
            target=lambda p: f"service {p.name}",
            summary=lambda p: f"Windows service '{p.name}' {p.op} (administrator)")
