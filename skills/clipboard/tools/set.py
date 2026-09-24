from __future__ import annotations

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.desktop import desktop_call


class P(Params):
    text: str


async def run(p: P, env: ToolEnv) -> ToolResult:
    r = await desktop_call(env, "/v1/clipboard/set", {"text": p.text})
    if isinstance(r, ToolResult):
        return r
    ok = bool(r.get("verified"))
    return ToolResult(ok, f"clipboard set ({len(p.text)} chars)", r, {"verified": ok})


TOOL = Tool(name="set", params=P, run=run, action="clipboard.write",
            target=lambda p: "clipboard", summary=lambda p: p.text[:200])
