from __future__ import annotations

from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult
from core.skills.browser_rpc import browser_call, page_evidence
from core.skills.urls import UrlBlocked, check_url


class P(Params):
    session_id: str
    url: str


def precheck(p: P, env: ToolEnv) -> None:
    if p.url:
        try:
            check_url(p.url)
        except UrlBlocked as e:
            raise PolicyDenied(str(e)) from None


async def run(p: P, env: ToolEnv) -> ToolResult:
    r = await browser_call(env, "POST", f"/v1/sessions/{p.session_id}/goto", {"url": p.url})
    if isinstance(r, ToolResult):
        return r
    return ToolResult(True, f"now at {r.get('title')!r} ({r.get('url')})", r, page_evidence(r),
                      untrusted=True)


TOOL = Tool(name="goto", params=P, run=run, action="browser.navigate", target=lambda p: p.url,
            precheck=precheck)
