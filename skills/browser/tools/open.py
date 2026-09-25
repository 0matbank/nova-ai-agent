from __future__ import annotations

from typing import Literal

from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult
from core.skills.browser_rpc import browser_call, page_evidence
from core.skills.urls import UrlBlocked, check_url


class P(Params):
    url: str | None = None
    profile: str | None = "default"   # agent-owned profile under sessions/browser/;
    #                                   None = throwaway in-memory profile
    headed: bool = False              # visible window (e.g. for the owner to log in)
    device: str | None = None         # "mobile" or a device name (plan §46 mobile test)
    engine: Literal["cli", "mcp"] = "cli"   # mcp = persistent session for multi-step work


def precheck(p: P, env: ToolEnv) -> None:
    if p.url:
        try:
            check_url(p.url)
        except UrlBlocked as e:
            raise PolicyDenied(str(e)) from None


async def run(p: P, env: ToolEnv) -> ToolResult:
    r = await browser_call(env, "POST", "/v1/sessions", p.model_dump())
    if isinstance(r, ToolResult):
        return r
    return ToolResult(True, f"browser session {r['session_id'][:8]} at {r.get('title')!r}", r,
                      page_evidence(r), untrusted=True)


TOOL = Tool(name="open", params=P, run=run, action="browser.navigate",
            target=lambda p: p.url or "about:blank",
            precheck=precheck)
