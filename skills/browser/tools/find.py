from __future__ import annotations

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.browser_rpc import browser_call


class P(Params):
    session_id: str
    text: str


async def run(p: P, env: ToolEnv) -> ToolResult:
    r = await browser_call(env, "POST", f"/v1/sessions/{p.session_id}/find", {"text": p.text})
    if isinstance(r, ToolResult):
        return r
    found = bool(r.get("matches"))
    return ToolResult(True, f"{p.text!r} {'found' if found else 'not found'} on the page", r,
                      {"found": found}, untrusted=True)


TOOL = Tool(name="find", params=P, run=run, action="browser.read",
            target=lambda p: p.session_id)
