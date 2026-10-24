"""Speaker volume / mute (V1.2) — no AI. Answers with what really changed."""

from __future__ import annotations

from pydantic import Field

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.desktop import desktop_call


class P(Params):
    level: int | None = Field(default=None, ge=0, le=100)
    change: int | None = Field(default=None, ge=-100, le=100)
    mute: bool | None = None


def _reads_only(p: P) -> bool:
    return p.level is None and p.change is None and p.mute is None


async def run(p: P, env: ToolEnv) -> ToolResult:
    r = await desktop_call(env, "/v1/device/volume", p.model_dump())
    if isinstance(r, ToolResult):
        return r
    if _reads_only(p):
        state = "🔇 mute" if r["muted"] else f"🔊 {r['level']}%"
        return ToolResult(True, f"Volume: {state}", r, r)
    b, a = r["before"], r["after"]
    fmt = lambda s: "🔇 mute" if s["muted"] else f"🔊 {s['level']}%"   # noqa: E731
    return ToolResult(True, f"Volume: {fmt(b)} → {fmt(a)}", r, {"before": b, "after": a})


TOOL = Tool(name="volume", params=P, run=run,
            action=lambda p, env: "device.read" if _reads_only(p) else "device.control",
            target=lambda p: "speakers")
