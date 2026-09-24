from __future__ import annotations

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.desktop import desktop_call


class P(Params):
    window: str
    max_depth: int = 6
    limit: int = 300


async def run(p: P, env: ToolEnv) -> ToolResult:
    r = await desktop_call(env, "/v1/ui/inspect",
                           {"window": p.window, "max_depth": p.max_depth, "limit": p.limit})
    if isinstance(r, ToolResult):
        return r
    return ToolResult(True, f"{len(r['controls'])} control(s) in {r['window']['title']!r}",
                      r, untrusted=True)


TOOL = Tool(name="inspect", params=P, run=run, action="ui.read", target=lambda p: p.window)
