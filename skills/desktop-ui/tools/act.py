"""Act on one element of one observation (plan §72 CU-3), for any app: click, set_text,
focus, toggle, select, expand, collapse, scroll_into_view — through UIA patterns.

The target is (observation_id, element number) from `observe`. The worker proves it is
still the same element right before the input — else "stale_target", never another
button with the same label. Permission is decided on what is really there NOW:
click/toggle/select/expand → ui.click (YELLOW: danger words in the control or its
window need approval); writing into a field that has text → ui.replace_text (old text
backed up first); a password field → RED ui.type_secret; a terminal/Run window → RED
ui.shell_input. The result separates
"delivered" from "the app visibly changed"."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.desktop import desktop_call
from core.skills.registry import SkillError
from core.skills.ui_policy import dangerous_label

Op = Literal["click", "set_text", "focus", "toggle", "select", "expand", "collapse",
             "scroll_into_view", "type"]
CLICKS = ("click", "toggle", "select", "expand", "collapse")


class P(Params):
    observation_id: str
    element: int
    op: Op = "click"
    text: str | None = None
    at: Literal["end", "cursor"] = "end"         # "type" only: append, or at the cursor
    # the caller knows this text is the owner's password/PIN: RED in any field
    secret: bool = False


async def _target(p: P, env: ToolEnv) -> dict | ToolResult:  # type: ignore[type-arg]
    return await desktop_call(env, "/v1/ui/target",
                              {"observation_id": p.observation_id, "element": p.element})


async def action(p: P, env: ToolEnv) -> str:
    t = await _target(p, env)
    if isinstance(t, ToolResult):
        if "stale_target" in t.summary:
            # the screen changed since the observation: nothing to approve — look again
            raise SkillError(t.summary)
        return "ui.shell_input"               # cannot tell what it is: fail closed (RED)
    if t["window"]["is_shell"] and p.op in ("click", "set_text", "type"):
        return "ui.shell_input"
    if p.op in ("focus", "scroll_into_view"):
        return "ui.focus"
    if p.op == "type":            # keyboard at the cursor: adds, never replaces
        return ("ui.type_secret" if t["element"].get("password") or p.secret
                else "ui.type")
    if p.op == "set_text":
        if t["element"].get("password") or p.secret:
            return "ui.type_secret"           # a password field: always the owner's call
        return "ui.replace_text" if t["element"].get("value") else "ui.type"
    return "ui.click"


async def safety_check(p: P, env: ToolEnv) -> tuple[bool, str]:
    t = await _target(p, env)
    if isinstance(t, ToolResult):
        return False, t.summary
    el, win = t["element"], t["window"]
    if p.op == "type":
        return True, "typed at the cursor: existing content stays"
    if p.op == "set_text":
        if el.get("value"):
            folder = env.paths.backups / "ui-text"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}.txt"
            path.write_text(str(el["value"]), encoding="utf-8")
            return True, f"previous content backed up to {path}"
        return True, "empty field"
    # the control's own words and the window/dialog it sits in (B-04: an "OK" in a
    # "Delete all files?" dialog is not harmless)
    for label in (f"{el.get('name', '')} {el.get('automation_id', '')}", win.get("title", "")):
        word = dangerous_label(label)
        if word:
            return False, (f"{el.get('name')!r} in {win.get('title')!r} looks "
                           f"consequential ({word!r})")
    return True, f"{el.get('type')} {el.get('name')!r} in {win.get('title')!r} looks safe"


async def run(p: P, env: ToolEnv) -> ToolResult:
    r = await desktop_call(env, "/v1/ui/act", p.model_dump(exclude={"secret"}))
    if isinstance(r, ToolResult):
        return r
    changed = r["effect"] == "changed"
    return ToolResult(True, f"{p.op} [{p.element}] {r['element']['name']!r} via {r['method']}"
                            + ("" if changed else " — the app showed no change"), r,
                      {"delivered": r["delivered"], "effect": r["effect"],
                       "after": (r.get("after") or {}).get("value"), "op": p.op})


def _summary(p: P) -> str:
    extra = (": (a password — hidden)" if p.secret else
             f": {p.text[:200]}" if p.op in ("set_text", "type") and p.text else "")
    return f"{p.op} element {p.element} of observation {p.observation_id}{extra}"


TOOL = Tool(name="act", params=P, run=run, action=action,
            target=lambda p: f"{p.observation_id}#{p.element}", summary=_summary,
            safety_check=safety_check)
