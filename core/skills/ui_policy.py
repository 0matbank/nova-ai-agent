"""Permission decisions for UI actions (plan §17, §18).

- A click whose target's name contains a danger word needs approval
  (YELLOW safety check fails → approval).
- Closing/deleting shortcuts need approval.
- Anything aimed at a terminal/shell window or the Run dialog is RED
  ui.shell_input — typing + Enter there would bypass the PowerShell classifier.
"""

from __future__ import annotations

import re
from typing import Any

from core.skills.api import ToolEnv, ToolResult
from core.skills.desktop import desktop_call

DANGER_WORDS = (
    "delete", "remove", "erase", "wipe", "format", "uninstall", "discard", "don't save",
    "dont save", "overwrite", "replace all", "clear all", "empty", "reset", "send", "submit",
    "post", "publish", "share", "pay", "purchase", "buy", "order", "checkout", "transfer",
    "confirm", "sign out", "log out", "logout", "shut down", "shutdown", "restart",
    "মুছুন", "মুছে", "ডিলিট", "পাঠান", "পাঠাও", "পেমেন্ট", "কিনুন", "নিশ্চিত",
)
DANGER_KEYS = {"alt+f4", "ctrl+w", "ctrl+shift+w", "ctrl+f4", "delete", "del", "shift+delete",
               "shift+del", "ctrl+shift+delete", "ctrl+enter", "ctrl+shift+enter",
               "ctrl+d", "ctrl+shift+q", "win+l"}


def dangerous_label(label: str) -> str | None:
    low = label.lower()
    for w in DANGER_WORDS:
        if re.search(rf"(^|\W){re.escape(w)}(\W|$)", low) or (not w.isascii() and w in low):
            return w
    return None


def normalize_combo(combo: str) -> str:
    return "+".join(p.strip().lower() for p in combo.split("+") if p.strip())


async def resolve(env: ToolEnv, window: str, name: str | None = None,
                  automation_id: str | None = None,
                  control_type: str | None = None) -> dict[str, Any] | ToolResult:
    return await desktop_call(env, "/v1/ui/resolve", {
        "window": window, "name": name, "automation_id": automation_id,
        "control_type": control_type})


async def is_shell_target(env: ToolEnv, window: str) -> bool:
    r = await resolve(env, window)
    # If the target cannot be resolved, fail closed (treat as shell → RED).
    return True if isinstance(r, ToolResult) else bool(r["window"].get("is_shell"))
