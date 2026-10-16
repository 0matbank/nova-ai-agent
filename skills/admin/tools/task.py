"""Run one of the broker's allowlisted fixed elevated commands (RED)."""

from __future__ import annotations

from pydantic import Field

from core.broker.skill import run_on_broker
from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult

DESCRIPTIONS = {
    "flush_dns": "DNS cache পরিষ্কার (ipconfig /flushdns)",
    "time_resync": "Windows-এর সময় internet-এর সাথে মেলানো (w32tm /resync)",
    "defender_quick_scan": "Microsoft Defender quick scan",
    "defender_update": "Microsoft Defender-এর virus definition update",
}


class P(Params):
    name: str = Field(pattern=r"^[a-z0-9_]{1,40}$", description="allowlisted task name")


def precheck(p: P, env: ToolEnv) -> None:
    allowed = env.config.workers.workers.privileged_broker.tasks
    if p.name not in allowed:
        raise PolicyDenied(f"'{p.name}' broker-এর allowlist-এ নেই "
                           f"(আছে: {', '.join(sorted(allowed)) or '—'})")


async def run(p: P, env: ToolEnv) -> ToolResult:
    return await run_on_broker("task", p, f"admin task {p.name}", env)


TOOL = Tool(name="task", params=P, run=run, action="admin.task",
            target=lambda p: f"admin task {p.name}",
            summary=lambda p: f"Administrator কাজ: {DESCRIPTIONS.get(p.name, p.name)}",
            precheck=precheck)
