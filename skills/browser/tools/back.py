from __future__ import annotations

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.browser_rpc import browser_call, page_evidence


class P(Params):
    session_id: str


async def run(p: P, env: ToolEnv) -> ToolResult:
    r = await browser_call(env, "POST", f"/v1/sessions/{p.session_id}/back")
    if isinstance(r, ToolResult):
        return r
    return ToolResult(True, f"back to {r.get('title')!r}", r, page_evidence(r), untrusted=True)


TOOL = Tool(name="back", params=P, run=run, action="browser.navigate",
            target=lambda p: p.session_id)
