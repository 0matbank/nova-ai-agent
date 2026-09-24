"""Graceful close (WM_CLOSE). The app may keep running if it asks to save —
that is reported, never forced (force = the RED `kill` tool)."""

from __future__ import annotations

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.desktop import desktop_call


class P(Params):
    app: str


async def run(p: P, env: ToolEnv) -> ToolResult:
    r = await desktop_call(env, "/v1/apps/close", {"app": p.app})
    if isinstance(r, ToolResult):
        return r
    if not r.get("was_running"):
        return ToolResult(True, f"{r['app']} was not running", r, {"still_running": []})
    ok = bool(r.get("verified"))
    msg = (f"{r['app']} closed" if ok else
           f"{r['app']} still running {r.get('still_running')} — it may be asking to save")
    return ToolResult(ok, msg, r, {"still_running": r.get("still_running")})


TOOL = Tool(name="close", params=P, run=run, action="app.close", target=lambda p: p.app)
