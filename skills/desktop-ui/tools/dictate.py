"""V1.2 dictation: type the owner's spoken words at the cursor of the field that has the
keyboard focus in the window in front. ui.type (BLUE) as ever; a password field or a
secret in the words → RED ui.type_secret; a terminal / Run dialog → RED ui.shell_input."""

from __future__ import annotations

import re

from pydantic import Field

from core.orchestrator.computer import mask, sensitive
from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.desktop import desktop_call
from core.skills.ui_policy import is_shell_target


class P(Params):
    text: str = Field(min_length=1, max_length=4000)
    window: str | None = None            # only into this window (exact title), if given


async def action(p: P, env: ToolEnv) -> str:
    r = await desktop_call(env, "/v1/ui/focused", {})
    if isinstance(r, ToolResult):
        return "ui.type"                      # nothing focused: run() reports it, types nothing
    if await is_shell_target(env, str(r["window"].get("title") or "")):
        return "ui.shell_input"
    if (r.get("control") or {}).get("password") or sensitive(p.text, p.text):
        return "ui.type_secret"
    return "ui.type"


NO_FIELD = ("⌨️ লিখিনি: {where} — cursor কোনো লেখার ঘরে নেই। লেখার বাক্সে একবার "
            "click করে আবার বলুন।")
HIDDEN_BOX = "⌨️ লিখিনি: {where} — এটা password-এর ঘর, কণ্ঠে বলা কথা এখানে লিখি না।"


def _owner_needed(r: ToolResult) -> ToolResult:
    """Nothing to retry: the cursor is not in a text box (or it is a password box) —
    the owner is told plainly where, and the task is not tried again."""
    m = re.search(r"(no_text_field|password_field): («[^»]*» \([^)]*\))?", r.summary)
    if m is None:
        return r
    where = m.group(2) or "সামনের window"
    text = (HIDDEN_BOX if m.group(1) == "password_field" else NO_FIELD).format(where=where)
    return ToolResult(False, text, r.data, {**r.evidence, "needs_owner": True})


async def run(p: P, env: ToolEnv) -> ToolResult:
    r = await desktop_call(env, "/v1/ui/dictate", {"text": p.text, "window": p.window})
    if isinstance(r, ToolResult):
        return _owner_needed(r)
    where = r["window"].get("title") or "?"
    ok = bool(r.get("verified"))
    note = "" if ok else (" — could not read the field back to check" if not r.get("readable")
                          else " — the text is not in the field: check it")
    return ToolResult(ok or not r.get("readable"),
                      f"typed {len(p.text)} chars at the cursor in «{where}»{note}", r,
                      {"window": where, "verified": ok})


def _target(p: P) -> str:
    return f"the focused field in «{p.window or 'the window in front'}»"


def _summary(p: P) -> str:
    return f"type at the cursor: {mask(p.text, p.text)[:300]}"


TOOL = Tool(name="dictate", params=P, run=run, action=action, target=_target,
            summary=_summary)
