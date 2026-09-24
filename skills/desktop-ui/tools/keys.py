"""Send a keyboard shortcut to a window (plan §17 priority 4). YELLOW:
closing/deleting shortcuts (Alt+F4, Ctrl+W, Delete, ...) need approval;
a terminal window is RED ui.shell_input."""

from __future__ import annotations

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.desktop import desktop_call
from core.skills.ui_policy import DANGER_KEYS, is_shell_target, normalize_combo


class P(Params):
    window: str
    keys: str


async def action(p: P, env: ToolEnv) -> str:
    return "ui.shell_input" if await is_shell_target(env, p.window) else "ui.keys"


async def safety_check(p: P, env: ToolEnv) -> tuple[bool, str]:
    combo = normalize_combo(p.keys)
    if combo in DANGER_KEYS:
        return False, f"{p.keys} closes/deletes/submits"
    return True, f"{p.keys} is not a destructive shortcut"


async def run(p: P, env: ToolEnv) -> ToolResult:
    r = await desktop_call(env, "/v1/ui/keys", {"window": p.window, "keys": p.keys})
    if isinstance(r, ToolResult):
        return r
    return ToolResult(True, f"sent {p.keys} to {p.window!r}", r,
                      {"window_still_open": r["window_still_open"],
                       "foreground_title": r["foreground_title"]})


TOOL = Tool(name="keys", params=P, run=run, action=action, target=lambda p: p.window,
            summary=lambda p: f"keys {p.keys} → {p.window!r}", safety_check=safety_check)
