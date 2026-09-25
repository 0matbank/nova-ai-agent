"""Calling the Browser Worker from a skill tool (plan §4, §17A)."""

from __future__ import annotations

import re
from typing import Any

from core.ipc.client import WorkerError, WorkerUnavailable
from core.skills.api import ToolEnv, ToolResult

NO_BROWSER = "Browser Worker চলছে না — browser-এর কাজ এখন করা যাচ্ছে না।"


async def browser_call(env: ToolEnv, method: str, path: str,
                       body: dict[str, Any] | None = None) -> dict[str, Any] | ToolResult:
    client = env.workers.get("browser")
    if client is None:
        return ToolResult(False, NO_BROWSER, {"needs_browser_worker": True})
    try:
        return await client.call(method, path, task_id=env.task_id, json=body)
    except WorkerUnavailable:
        return ToolResult(False, NO_BROWSER, {"needs_browser_worker": True})
    except WorkerError as e:
        return ToolResult(False, f"browser: {_readable(str(e))}")


def _readable(msg: str) -> str:
    """'browser: HTTP 422 {"detail": "fill failed: Timeout…\\nCall log…"}' → first line."""
    m = re.search(r'"detail"\s*:\s*"((?:[^"\\]|\\.)*)', msg)
    text = m.group(1).encode().decode("unicode_escape", "ignore") if m else msg
    text = re.sub(r"\x1b\[[0-9;]*m", "", text).removeprefix("browser: ")
    return text.splitlines()[0][:200] if text else msg[:200]


def page_evidence(r: dict[str, Any]) -> dict[str, Any]:
    """Proof for the Verifier (plan §54): where the browser actually is."""
    return {"url": r.get("url"), "title": r.get("title"), "challenge": r.get("challenge")}
