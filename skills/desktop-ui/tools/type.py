"""Type text into a control. Default APPENDS — existing content is kept and
verified to still be there. replace=true → ui.replace_text (YELLOW: the old
content is backed up first). Terminal / Run dialog → RED ui.shell_input."""

from __future__ import annotations

from datetime import UTC, datetime

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.desktop import desktop_call
from core.skills.ui_policy import is_shell_target


class P(Params):
    window: str
    text: str
    name: str | None = None
    automation_id: str | None = None
    control_type: str | None = None
    replace: bool = False


async def action(p: P, env: ToolEnv) -> str:
    if await is_shell_target(env, p.window):
        return "ui.shell_input"
    return "ui.replace_text" if p.replace else "ui.type"


async def safety_check(p: P, env: ToolEnv) -> tuple[bool, str]:
    r = await desktop_call(env, "/v1/ui/value", {
        "window": p.window, "name": p.name, "automation_id": p.automation_id,
        "control_type": p.control_type})
    if isinstance(r, ToolResult):
        return False, f"cannot read current content: {r.summary}"
    folder = env.paths.backups / "ui-text"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}.txt"
    path.write_text(str(r.get("value") or ""), encoding="utf-8")
    return True, f"previous content backed up to {path}"


async def run(p: P, env: ToolEnv) -> ToolResult:
    r = await desktop_call(env, "/v1/ui/type", p.model_dump())
    if isinstance(r, ToolResult):
        return r
    ok = bool(r.get("verified"))
    return ToolResult(ok, f"typed {len(p.text)} chars via {r['method']}"
                          + ("" if ok else " — could not verify"), r,
                      {"after": r.get("after"), "previous_content_kept":
                       r.get("previous_content_kept")})


def _target(p: P) -> str:
    return f"{p.window} / {p.name or p.automation_id or p.control_type or 'focused'}"


def _summary(p: P) -> str:
    return f"{'REPLACE content with' if p.replace else 'type'}: {p.text[:300]}"


TOOL = Tool(name="type", params=P, run=run, action=action, target=_target, summary=_summary,
            safety_check=safety_check)
