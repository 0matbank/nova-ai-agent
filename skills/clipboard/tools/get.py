"""Read the clipboard (GREEN). Content is untrusted and passes the redactor —
a copied password/token never leaves the PC in clear text."""

from __future__ import annotations

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.desktop import desktop_call


class P(Params):
    pass


async def run(p: P, env: ToolEnv) -> ToolResult:
    r = await desktop_call(env, "/v1/clipboard/get")
    if isinstance(r, ToolResult):
        return r
    if not r.get("has_text"):
        return ToolResult(True, "clipboard has no text", r)
    return ToolResult(True, f"clipboard: {len(r['text'])} chars", r, untrusted=True)


TOOL = Tool(name="get", params=P, run=run, action="clipboard.read",
            target=lambda p: "clipboard")
