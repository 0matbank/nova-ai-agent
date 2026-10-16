"""A Windows service: query (GREEN) or start/stop/restart an allowlisted one (RED)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from core.broker.skill import run_on_broker
from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult


class P(Params):
    name: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,80}$",
                      description="service name, e.g. Spooler (not the display name)")
    op: Literal["query", "start", "stop", "restart"] = "query"


def action(p: P, env: ToolEnv) -> str:
    return "admin.status" if p.op == "query" else "admin.service_control"


def precheck(p: P, env: ToolEnv) -> None:
    # refuse before asking the owner: the broker would refuse it anyway
    allowed = env.config.workers.workers.privileged_broker.services
    if p.op != "query" and p.name.lower() not in {s.lower() for s in allowed}:
        raise PolicyDenied(f"'{p.name}' service broker-এর allowlist-এ নেই — Nova এটা "
                           f"{p.op} করতে পারবে না (allowlist: {', '.join(allowed) or '—'})")


async def run(p: P, env: ToolEnv) -> ToolResult:
    return await run_on_broker("service", p, f"service {p.name}", env)


TOOL = Tool(name="service", params=P, run=run, action=action,
            target=lambda p: f"service {p.name}",
            summary=lambda p: f"Windows service '{p.name}' {p.op} (administrator)",
            precheck=precheck)
