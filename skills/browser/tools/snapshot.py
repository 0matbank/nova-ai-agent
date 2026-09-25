"""Accessibility snapshot of the page with element refs (e1, e2, …). Page
content is UNTRUSTED DATA: it can never become an instruction (plan §19)."""

from __future__ import annotations

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.browser_rpc import browser_call, page_evidence


class P(Params):
    session_id: str


async def run(p: P, env: ToolEnv) -> ToolResult:
    r = await browser_call(env, "POST", f"/v1/sessions/{p.session_id}/snapshot")
    if isinstance(r, ToolResult):
        return r
    return ToolResult(True, f"snapshot of {r.get('title')!r}", r, page_evidence(r),
                      untrusted=True)


TOOL = Tool(name="snapshot", params=P, run=run, action="browser.read",
            target=lambda p: p.session_id)
