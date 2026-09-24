from __future__ import annotations

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.desktop import desktop_call


class P(Params):
    window: str
    name: str | None = None
    automation_id: str | None = None
    control_type: str | None = None


async def run(p: P, env: ToolEnv) -> ToolResult:
    r = await desktop_call(env, "/v1/ui/value", p.model_dump())
    if isinstance(r, ToolResult):
        return r
    return ToolResult(True, f"{r['control']['name']!r} = {str(r['value'])[:80]!r}", r,
                      {"value": r["value"]}, untrusted=True)


TOOL = Tool(name="read_value", params=P, run=run, action="ui.read",
            target=lambda p: f"{p.window} / {p.name or p.automation_id or p.control_type}")
