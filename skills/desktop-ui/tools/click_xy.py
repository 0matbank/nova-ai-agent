"""Raw coordinate click — LAST RESORT (plan §15, §17), always RED."""

from __future__ import annotations

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.desktop import desktop_call


class P(Params):
    x: int
    y: int


async def run(p: P, env: ToolEnv) -> ToolResult:
    r = await desktop_call(env, "/v1/ui/click_xy", {"x": p.x, "y": p.y})
    if isinstance(r, ToolResult):
        return r
    under = r.get("under_cursor") or {}
    return ToolResult(True, f"clicked ({p.x},{p.y}) on {under.get('name')!r}", r,
                      {"under_cursor": under})


TOOL = Tool(name="click_xy", params=P, run=run, action="ui.click_xy",
            target=lambda p: f"({p.x},{p.y})",
            summary=lambda p: f"RAW coordinate click at ({p.x},{p.y}) — last resort")
