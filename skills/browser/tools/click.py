"""Click an element by snapshot ref. YELLOW: allowed only when the element's
text has no danger word (buy/pay/send/delete/confirm… — plan §36)."""

from __future__ import annotations

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.browser_rpc import browser_call, page_evidence
from core.skills.ui_policy import dangerous_label


class P(Params):
    session_id: str
    ref: str


async def safety_check(p: P, env: ToolEnv) -> tuple[bool, str]:
    r = await browser_call(env, "POST", f"/v1/sessions/{p.session_id}/describe", {"ref": p.ref})
    if isinstance(r, ToolResult):
        return False, r.summary
    el = r.get("element") or {}
    label = str(el.get("text", "")) if isinstance(el, dict) else str(el)
    word = dangerous_label(label)
    if word:
        return False, f"element {label[:60]!r} looks consequential ({word!r})"
    return True, f"element {label[:60]!r} looks safe"


async def run(p: P, env: ToolEnv) -> ToolResult:
    r = await browser_call(env, "POST", f"/v1/sessions/{p.session_id}/click", {"ref": p.ref})
    if isinstance(r, ToolResult):
        return r
    return ToolResult(True, f"clicked {p.ref}; now at {r.get('title')!r}", r, page_evidence(r),
                      untrusted=True)


TOOL = Tool(name="click", params=P, run=run, action="browser.click",
            target=lambda p: f"{p.session_id[:8]}/{p.ref}", safety_check=safety_check)
