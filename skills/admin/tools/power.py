"""Restart / shut down the PC (RED, always with a delay so it can be cancelled), or
cancel a scheduled one (GREEN)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from core.broker.skill import run_on_broker
from core.skills.api import Params, Tool, ToolEnv, ToolResult


class P(Params):
    op: Literal["restart", "shutdown", "cancel"]
    delay_seconds: int = Field(60, ge=60, le=3600, description="time left to cancel")


def action(p: P, env: ToolEnv) -> str:
    return {"restart": "system.restart", "shutdown": "system.shutdown"}.get(
        p.op, "admin.power_cancel")


def _summary(p: P) -> str:
    if p.op == "cancel":
        return "scheduled restart/shutdown বাতিল"
    what = "Restart" if p.op == "restart" else "Shutdown"
    return (f"PC {what} — {p.delay_seconds // 60} মিনিট পর। খোলা unsaved কাজ হারাতে পারে। "
            "এর মধ্যে 'restart cancel' লিখে থামানো যাবে।")


async def run(p: P, env: ToolEnv) -> ToolResult:
    return await run_on_broker("power", p, f"pc {p.op}", env)


TOOL = Tool(name="power", params=P, run=run, action=action, target=lambda p: f"pc {p.op}",
            summary=_summary)
