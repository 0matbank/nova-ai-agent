"""Media keys (V1.2) — play/pause, next, previous, stop for whatever player is in use."""

from __future__ import annotations

from typing import Literal

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.desktop import desktop_call

NAMES = {"play_pause": "⏯ play/pause", "next": "⏭ next", "previous": "⏮ previous",
         "stop": "⏹ stop"}


class P(Params):
    action: Literal["play_pause", "next", "previous", "stop"]


async def run(p: P, env: ToolEnv) -> ToolResult:
    r = await desktop_call(env, "/v1/device/media", {"action": p.action})
    if isinstance(r, ToolResult):
        return r
    # a key press has no readable result here: said plainly, never "verified"
    return ToolResult(True, f"Media key sent: {NAMES[p.action]}", r, {"sent": p.action})


TOOL = Tool(name="media", params=P, run=run, action="device.control",
            target=lambda p: "media keys")
