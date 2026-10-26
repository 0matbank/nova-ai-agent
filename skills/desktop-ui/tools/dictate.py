"""V1.2 dictation: type the owner's spoken words at the cursor of the field that has the
keyboard focus in the window in front. ui.type (BLUE) as ever; a password field or a
secret in the words → RED ui.type_secret; a terminal / Run dialog → RED ui.shell_input."""

from __future__ import annotations

import re

from pydantic import Field

from core.log import get_logger
from core.orchestrator.computer import mask, sensitive
from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.desktop import desktop_call
from core.skills.ui_policy import is_shell_target

_log = get_logger("core")


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
BUSY_HANDS = ("⌨️ লিখিনি: আপনি keyboard/mouse ব্যবহার করছিলেন। হাত সরিয়ে রেখে আবার "
              "Ctrl+Alt+D চেপে বলুন।")
UNCHECKED = ("⌨️ «{where}»-এ লিখেছি, কিন্তু ঘরে পুরোটা মিলিয়ে পাইনি — একবার দেখে নিন। "
             "আবার লিখিনি, যাতে দুবার না হয়।")
# the worker waits this long for the owner's hands to be still, then types
OWNER_WAIT_SECONDS = 8.0


def _owner_needed(r: ToolResult) -> ToolResult:
    """Nothing to retry: the cursor is not in a text box (or it is a password box, or
    the owner kept using the keyboard/mouse) — the owner is told plainly, and the task
    is not tried again."""
    m = re.search(r"(no_text_field|password_field|user_active): («[^»]*» \([^)]*\))?",
                  r.summary)
    if m is None:
        return r
    where = m.group(2) or "সামনের window"
    text = {"password_field": HIDDEN_BOX, "user_active": BUSY_HANDS}.get(
        m.group(1), NO_FIELD).format(where=where)
    return ToolResult(False, text, r.data, {**r.evidence, "needs_owner": True})


async def run(p: P, env: ToolEnv) -> ToolResult:
    r = await desktop_call(env, "/v1/ui/dictate", {"text": p.text, "window": p.window},
                           timeout=OWNER_WAIT_SECONDS + 15 + len(p.text) * 0.02)
    if isinstance(r, ToolResult):
        return _owner_needed(r)
    where = r["window"].get("title") or "?"
    ok = bool(r.get("verified"))
    if r.get("readable") and not ok:
        # the words were typed: typing them again could put them in twice (owner
        # 2026-10-26, task #158) — say so once, never retry
        _log.info(f"dictation typed but not confirmed in the field: {r.get('check')}",
                  extra={"action": "ui.dictate", "status": "unconfirmed"})
        return ToolResult(False, UNCHECKED.format(where=where), r,
                          {"window": where, "verified": False, "needs_owner": True})
    note = "" if ok else " — could not read the field back to check"
    return ToolResult(True, f"typed {len(p.text)} chars at the cursor in «{where}»{note}", r,
                      {"window": where, "verified": ok})


def _target(p: P) -> str:
    return f"the focused field in «{p.window or 'the window in front'}»"


def _summary(p: P) -> str:
    return f"type at the cursor: {mask(p.text, p.text)[:300]}"


TOOL = Tool(name="dictate", params=P, run=run, action=action, target=_target,
            summary=_summary)
