"""Choose an option in a dropdown (BLUE browser.select — not a submit)."""

from __future__ import annotations

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.browser_rpc import browser_call, page_evidence


class P(Params):
    session_id: str
    ref: str
    value: str


async def run(p: P, env: ToolEnv) -> ToolResult:
    r = await browser_call(env, "POST", f"/v1/sessions/{p.session_id}/select",
                           {"ref": p.ref, "value": p.value})
    if isinstance(r, ToolResult):
        return r
    return ToolResult(True, f"selected {p.value!r} in {p.ref}", r, page_evidence(r),
                      untrusted=True)


TOOL = Tool(name="select", params=P, run=run, action="browser.select",
            target=lambda p: f"{p.session_id[:8]}/{p.ref}")
