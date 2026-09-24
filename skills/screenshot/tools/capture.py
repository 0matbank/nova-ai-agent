"""Screenshot of the user's desktop (GREEN). Saved locally in full resolution;
a small JPEG preview is returned for chat. If the PC is locked the task waits
in WAITING_DESKTOP (plan §5)."""

from __future__ import annotations

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.desktop import desktop_call


class P(Params):
    monitor: int = 0
    preview_width: int = 1280


async def run(p: P, env: ToolEnv) -> ToolResult:
    r = await desktop_call(env, "/v1/screenshot",
                           {"monitor": p.monitor, "preview_width": p.preview_width})
    if isinstance(r, ToolResult):
        return r
    note = " — image looks blank (secure desktop?)" if r.get("blank") else ""
    return ToolResult(not r.get("blank"), f"screenshot {r['width']}x{r['height']}{note}", r,
                      {"path": r["path"], "width": r["width"], "height": r["height"],
                       "blank": r.get("blank")})


TOOL = Tool(name="capture", params=P, run=run, action="screenshot",
            target=lambda p: f"monitor {p.monitor}")
