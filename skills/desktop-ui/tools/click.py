"""Click a control through UIA patterns (Invoke/Toggle/Select/Expand) — no
mouse coordinates. YELLOW: allowed only if the control's name contains no
danger word (delete/send/pay/...); a terminal window is RED ui.shell_input."""

from __future__ import annotations

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.desktop import desktop_call
from core.skills.ui_policy import dangerous_label, is_shell_target, resolve


class P(Params):
    window: str
    name: str | None = None
    automation_id: str | None = None
    control_type: str | None = None


async def action(p: P, env: ToolEnv) -> str:
    return "ui.shell_input" if await is_shell_target(env, p.window) else "ui.click"


async def safety_check(p: P, env: ToolEnv) -> tuple[bool, str]:
    r = await resolve(env, p.window, p.name, p.automation_id, p.control_type)
    if isinstance(r, ToolResult):
        return False, r.summary
    label = " ".join(filter(None, [r["control"]["name"], r["control"]["automation_id"],
                                   p.name or ""]))
    word = dangerous_label(label)
    if word:
        return False, f"control {r['control']['name']!r} looks consequential ({word!r})"
    return True, f"control {r['control']['name']!r} looks safe"


async def run(p: P, env: ToolEnv) -> ToolResult:
    r = await desktop_call(env, "/v1/ui/click", p.model_dump())
    if isinstance(r, ToolResult):
        return r
    return ToolResult(True, f"clicked {r['control']['name']!r} via {r['method']}", r,
                      {"window_still_open": r["window_still_open"],
                       "foreground_title": r["foreground_title"]})


TOOL = Tool(name="click", params=P, run=run, action=action,
            target=lambda p: f"{p.window} / {p.name or p.automation_id or p.control_type}",
            summary=lambda p: f"click {p.name or p.automation_id!r} in {p.window!r}",
            safety_check=safety_check)
