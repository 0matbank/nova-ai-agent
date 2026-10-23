from __future__ import annotations

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.desktop import desktop_call


class P(Params):
    app: str
    args: list[str] = []


async def run(p: P, env: ToolEnv) -> ToolResult:
    r = await desktop_call(env, "/v1/apps/open", {"app": p.app, "args": p.args})
    if isinstance(r, ToolResult):
        return r
    ok = bool(r.get("verified"))
    if r.get("window_titled"):
        state = "window is open"            # a Store app: its own window, no exe to watch
    elif r.get("still_starting"):
        state = "is starting (window not shown yet)"
    else:
        state = "is running" if ok else "did not start"
    return ToolResult(ok, f"{r['app']} {state} (pids {r.get('running_pids')})", r,
                      {"running_pids": r.get("running_pids"), "new_pids": r.get("new_pids"),
                       "window_titled": bool(r.get("window_titled"))})


TOOL = Tool(name="open", params=P, run=run, action="app.open", target=lambda p: p.app,
            summary=lambda p: f"open {p.app} {' '.join(p.args)}".strip())
