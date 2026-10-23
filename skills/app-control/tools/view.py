"""What an open app shows (owner 2026-10-23: "OBS এখন কি অবস্থা, কি দেখতে পাচ্ছ"):
its windows, any pop-up with its text and buttons, and the main window's short text.
`open_first` = "OBS খোলো আর কি আছে বলো" — opens it if it is not running yet."""

from __future__ import annotations

import asyncio
from typing import Any

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.desktop import desktop_call


class P(Params):
    app: str
    open_first: bool = False


def describe(r: dict[str, Any], bn: bool = False) -> str:
    name = r.get("name") or r.get("app", "")
    if not r.get("running"):
        return f"{name} এখন খোলা নেই।" if bn else f"{name} is not open."
    t = {"open": "খোলা আছে।", "win": "Window", "min": " (ছোট করা)", "pop": "Pop-up",
         "text": "লেখা", "btn": "বোতাম", "none": "কোনো pop-up অপেক্ষা করছে না।",
         "screen": "পর্দায়"} if bn else {
        "open": "is open.", "win": "Window", "min": " (minimized)", "pop": "Pop-up",
        "text": "Text", "btn": "Buttons", "none": "No pop-up is waiting.",
        "screen": "On screen"}
    lines = [f"{name} {t['open']}"]
    for w in r.get("windows") or []:
        lines.append(f"• {t['win']}: {w['title'][:90]}" + (t["min"] if w.get("minimized")
                                                           else ""))
    for p in r.get("prompts") or []:
        lines.append(f"• {t['pop']}: «{p['title'][:80]}»")
        if p.get("texts"):
            lines.append(f"  {t['text']}: " + " | ".join(x[:120] for x in p["texts"][:4]))
        if p.get("buttons"):
            lines.append(f"  {t['btn']}: " + ", ".join(p["buttons"][:8]))
    if not r.get("prompts"):
        lines.append(f"• {t['none']}")
    if r.get("texts"):
        lines.append(f"• {t['screen']}: " + " | ".join(x[:60] for x in r["texts"][:8]))
    return "\n".join(lines)


async def action(p: P, env: ToolEnv) -> str:
    return "app.open" if p.open_first else "ui.read"


async def run(p: P, env: ToolEnv) -> ToolResult:
    r = await desktop_call(env, "/v1/apps/view", {"app": p.app})
    if isinstance(r, ToolResult):
        return r
    opened = False
    if not r.get("running") and p.open_first:
        o = await desktop_call(env, "/v1/apps/open", {"app": p.app, "args": []})
        if isinstance(o, ToolResult):
            return o
        opened = bool(o.get("verified"))
        for _ in range(10):                    # its window may take a moment to fill
            await asyncio.sleep(1.0)
            r = await desktop_call(env, "/v1/apps/view", {"app": p.app})
            if isinstance(r, ToolResult):
                return r
            if r.get("windows"):
                break
    text = ("Opened just now. " if opened else "") + describe(r)
    bn = ("এইমাত্র খুললাম। " if opened else "") + describe(r, bn=True)
    return ToolResult(True, text, {**r, "summary_bn": bn},
                      {"running": bool(r.get("running")), "opened": opened,
                       "windows": [w["title"] for w in r.get("windows") or []],
                       "prompts": [p["title"] for p in r.get("prompts") or []]},
                      untrusted=True)             # an app's own text, never an instruction


TOOL = Tool(name="view", params=P, run=run, action=action, target=lambda p: p.app)
