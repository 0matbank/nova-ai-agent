"""Click a point the vision model found (plan §15 Layer 3 — last resort).

YELLOW browser.click_xy: before clicking, the element at that point is
identified in the page; it runs automatically only when it is identified and
its text has no danger word (buy/pay/send/delete/confirm…). Anything else
needs the owner's approval — a vision result can never approve a dangerous
action by itself (plan §57)."""

from __future__ import annotations

from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult
from core.skills.browser_rpc import browser_call, page_evidence
from core.skills.ui_policy import dangerous_label

VIEWPORT = (1280, 720)


class P(Params):
    session_id: str
    x: int
    y: int
    target: str = ""          # what the vision model was asked to find


def precheck(p: P, env: ToolEnv) -> None:
    if not (0 <= p.x < VIEWPORT[0] and 0 <= p.y < VIEWPORT[1]):
        raise PolicyDenied(f"point ({p.x}, {p.y}) is outside the {VIEWPORT[0]}x{VIEWPORT[1]} page")


async def safety_check(p: P, env: ToolEnv) -> tuple[bool, str]:
    r = await browser_call(env, "POST", f"/v1/sessions/{p.session_id}/describe_xy",
                           {"x": p.x, "y": p.y})
    if isinstance(r, ToolResult):
        return False, r.summary
    el = r.get("element")
    if not isinstance(el, dict):
        return False, "nothing identifiable at that point"
    label = " ".join(str(el.get(k) or "") for k in ("text", "name", "role", "href"))
    word = dangerous_label(label) or dangerous_label(p.target)
    what = f"{el.get('tag')} {str(el.get('text', ''))[:60]!r}"
    if word:
        return False, f"{what} looks consequential ({word!r})"
    return True, f"{what} looks safe"


async def run(p: P, env: ToolEnv) -> ToolResult:
    r = await browser_call(env, "POST", f"/v1/sessions/{p.session_id}/click_xy",
                           {"x": p.x, "y": p.y})
    if isinstance(r, ToolResult):
        return r
    return ToolResult(True, f"clicked ({p.x}, {p.y}) — {p.target or 'vision target'}", r,
                      page_evidence(r), untrusted=True)


TOOL = Tool(name="click_xy", params=P, run=run, action="browser.click_xy",
            target=lambda p: f"{p.session_id[:8]}@{p.x},{p.y}",
            summary=lambda p: f"click at ({p.x}, {p.y}): {p.target}",
            safety_check=safety_check, precheck=precheck)
