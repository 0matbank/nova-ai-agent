from __future__ import annotations

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.desktop import desktop_call


class P(Params):
    window: str


async def run(p: P, env: ToolEnv) -> ToolResult:
    r = await desktop_call(env, "/v1/ui/focus", {"window": p.window})
    if isinstance(r, ToolResult):
        return r
    ok = bool(r.get("verified"))
    return ToolResult(ok, f"{r['window']['title']!r} {'is' if ok else 'is NOT'} in front", r,
                      {"foreground": ok})


TOOL = Tool(name="focus", params=P, run=run, action="ui.focus", target=lambda p: p.window)
