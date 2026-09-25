from __future__ import annotations

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.browser_rpc import browser_call


class P(Params):
    session_id: str


async def run(p: P, env: ToolEnv) -> ToolResult:
    r = await browser_call(env, "DELETE", f"/v1/sessions/{p.session_id}")
    if isinstance(r, ToolResult):
        return r
    return ToolResult(True, "browser session closed", r, {"closed": True})


TOOL = Tool(name="close", params=P, run=run, action="browser.navigate",
            target=lambda p: p.session_id)
