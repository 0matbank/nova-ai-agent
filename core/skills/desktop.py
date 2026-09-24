"""Calling the Desktop Worker from a skill tool (plan §4, §5)."""

from __future__ import annotations

from typing import Any

from core.ipc.client import WorkerError, WorkerUnavailable
from core.skills.api import ToolEnv, ToolResult

NEEDS_DESKTOP = ("এই কাজের desktop অংশ চালাতে user login / PC unlock দরকার "
                 "(Desktop Worker চলছে না)। Background অংশ চালু আছে।")


async def desktop_call(env: ToolEnv, path: str,
                       body: dict[str, Any]) -> dict[str, Any] | ToolResult:
    client = env.workers.get("desktop")
    if client is None:
        return ToolResult(False, NEEDS_DESKTOP, {"needs_desktop": True})
    try:
        return await client.call("POST", path, task_id=env.task_id, json=body)
    except WorkerUnavailable:
        return ToolResult(False, NEEDS_DESKTOP, {"needs_desktop": True})
    except WorkerError as e:
        return ToolResult(False, f"desktop worker error: {e}")
