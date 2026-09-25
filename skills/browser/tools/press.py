"""Press a key in the page. Enter submits forms → browser.submit (approval
unless you use fill(submit=true) on a search box); other keys are BLUE."""

from __future__ import annotations

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.browser_rpc import browser_call, page_evidence

SUBMIT_KEYS = {"enter", "return", "numpadenter"}


class P(Params):
    session_id: str
    key: str


def action(p: P, env: ToolEnv) -> str:
    return "browser.submit" if p.key.lower() in SUBMIT_KEYS else "browser.fill"


async def safety_check(p: P, env: ToolEnv) -> tuple[bool, str]:
    return False, "Enter may submit a form — use fill(submit=true) on a search box instead"


async def run(p: P, env: ToolEnv) -> ToolResult:
    r = await browser_call(env, "POST", f"/v1/sessions/{p.session_id}/press", {"key": p.key})
    if isinstance(r, ToolResult):
        return r
    return ToolResult(True, f"pressed {p.key}", r, page_evidence(r), untrusted=True)


TOOL = Tool(name="press", params=P, run=run, action=action, target=lambda p: p.key,
            safety_check=safety_check)
