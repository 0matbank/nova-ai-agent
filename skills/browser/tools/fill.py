"""Type into a field. Password fields are RED (browser.fill_secret). With
submit=true it becomes browser.submit (YELLOW): automatic only for a search
box — submitting any other form needs approval."""

from __future__ import annotations

from typing import Any

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.browser_rpc import browser_call, page_evidence

SEARCH_WORDS = ("search", "খুঁজ", "সার্চ", "find", "query")


class P(Params):
    session_id: str
    ref: str
    text: str
    submit: bool = False


async def _element(p: P, env: ToolEnv) -> dict[str, Any] | None:
    r = await browser_call(env, "POST", f"/v1/sessions/{p.session_id}/describe", {"ref": p.ref})
    if isinstance(r, ToolResult):
        return None
    el = r.get("element")
    return el if isinstance(el, dict) else None


async def action(p: P, env: ToolEnv) -> str:
    el = await _element(p, env)
    if el is None or str(el.get("type", "")).lower() == "password":
        return "browser.fill_secret"          # unknown element → fail closed
    return "browser.submit" if p.submit else "browser.fill"


async def safety_check(p: P, env: ToolEnv) -> tuple[bool, str]:
    el = await _element(p, env) or {}
    label = " ".join(str(el.get(k) or "") for k in ("type", "role", "name", "placeholder",
                                                   "text")).lower()
    if (any(w in label for w in SEARCH_WORDS) or el.get("type") == "search"
            or el.get("role") == "searchbox" or el.get("name") == "q"):
        return True, "search box"
    return False, "submitting a form (not a search box)"


async def run(p: P, env: ToolEnv) -> ToolResult:
    r = await browser_call(env, "POST", f"/v1/sessions/{p.session_id}/fill", p.model_dump(
        exclude={"session_id"}))
    if isinstance(r, ToolResult):
        return r
    return ToolResult(True, f"filled {p.ref}" + (" and submitted" if p.submit else ""), r,
                      page_evidence(r), untrusted=True)


TOOL = Tool(name="fill", params=P, run=run, action=action,
            target=lambda p: f"{p.session_id[:8]}/{p.ref}",
            summary=lambda p: f"type into {p.ref}" + (" and SUBMIT" if p.submit else ""),
            safety_check=safety_check)
