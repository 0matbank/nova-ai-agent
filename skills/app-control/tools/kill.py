"""Force-kill an app (RED: unsaved work is lost)."""

from __future__ import annotations

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.desktop import desktop_call


class P(Params):
    app: str


async def run(p: P, env: ToolEnv) -> ToolResult:
    r = await desktop_call(env, "/v1/apps/kill", {"app": p.app})
    if isinstance(r, ToolResult):
        return r
    ok = bool(r.get("verified"))
    return ToolResult(ok, f"{r['app']}: killed {len(r.get('killed', []))} process(es)", r,
                      {"still_running": r.get("still_running")})


TOOL = Tool(name="kill", params=P, run=run, action="process.kill", target=lambda p: p.app,
            summary=lambda p: f"FORCE KILL {p.app} — unsaved work will be lost")
