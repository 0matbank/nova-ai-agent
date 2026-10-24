"""Screen brightness (V1.2): DDC/CI for external monitors, WMI for laptop panels."""

from __future__ import annotations

from pydantic import Field

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.desktop import desktop_call


class P(Params):
    level: int | None = Field(default=None, ge=0, le=100)
    change: int | None = Field(default=None, ge=-100, le=100)


async def run(p: P, env: ToolEnv) -> ToolResult:
    r = await desktop_call(env, "/v1/device/brightness", p.model_dump())
    if isinstance(r, ToolResult):
        return r
    parts = [f"{s['screen']}: {s['before']} → {s['after']}" for s in r["screens"]]
    return ToolResult(True, "Brightness — " + "; ".join(parts), r, r)


TOOL = Tool(name="brightness", params=P, run=run,
            action=lambda p, env: "device.read" if p.level is None and p.change is None
            else "device.control",
            target=lambda p: "screens")
