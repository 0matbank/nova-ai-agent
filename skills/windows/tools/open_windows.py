"""What is open on the screen (V1.1): visible windows grouped by app, from the Desktop
Worker — not the process list ("কোন কোন app খোলা আছে" ≠ "কি কি process চলছে")."""

from __future__ import annotations

from typing import Any

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.desktop import desktop_call


class P(Params):
    max_titles: int = 3


def describe(apps: list[dict[str, Any]], max_titles: int = 3) -> str:
    if not apps:
        return "No app window is open on the screen."
    lines = [f"{len(apps)} app(s) open:"]
    for a in apps:
        wins = a.get("windows") or []
        titles = [w["title"] for w in wins if w.get("title") and w["title"] != a["name"]]
        mark = " (সামনে)" if a.get("foreground") else ""
        extra = f" — {len(wins)} window" if len(wins) > 1 else ""
        shown = "; ".join(t[:70] for t in titles[:max_titles])
        more = f" … +{len(titles) - max_titles}" if len(titles) > max_titles else ""
        lines.append(f"• {a['name']}{mark}{extra}" + (f": {shown}{more}" if shown else ""))
    return "\n".join(lines)


async def run(p: P, env: ToolEnv) -> ToolResult:
    r = await desktop_call(env, "/v1/apps/visible")
    if isinstance(r, ToolResult):
        return r
    apps = list(r.get("apps") or [])
    return ToolResult(True, describe(apps, p.max_titles),
                      {"apps": apps, "windows": r.get("windows", 0)},
                      {"apps": [a["name"] for a in apps], "windows": r.get("windows", 0)},
                      untrusted=True)            # window titles are the apps' text


TOOL = Tool(name="open_windows", params=P, run=run, action="ui.read",
            target=lambda p: "desktop")
