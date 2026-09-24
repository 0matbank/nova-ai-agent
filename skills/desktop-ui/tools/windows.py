from __future__ import annotations

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.desktop import desktop_call


class P(Params):
    pass


async def run(p: P, env: ToolEnv) -> ToolResult:
    r = await desktop_call(env, "/v1/ui/windows")
    if isinstance(r, ToolResult):
        return r
    wins = r["windows"]
    fg = next((w["title"] for w in wins if w.get("foreground")), None)
    return ToolResult(True, f"{len(wins)} window(s); foreground: {fg}", {"windows": wins},
                      untrusted=True)


TOOL = Tool(name="windows", params=P, run=run, action="ui.read", target=lambda p: "desktop")
