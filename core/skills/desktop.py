"""Calling the Desktop Worker from a skill tool (plan §4, §5).

If the desktop part cannot run right now (nobody logged in, or the PC is
locked) the tool raises DesktopUnavailable; the task engine parks the task in
WAITING_DESKTOP and resumes it automatically after unlock. Background parts of
other tasks keep running.
"""

from __future__ import annotations

from typing import Any

from core.ipc.client import WorkerDesktopLocked, WorkerError, WorkerUnavailable
from core.skills.api import ToolEnv, ToolResult

NEEDS_DESKTOP = "এই কাজের desktop অংশ চালাতে PC unlock দরকার। Background অংশ চালু আছে।"


class DesktopUnavailable(Exception):
    def __init__(self, reason: str) -> None:       # "locked" | "not_running"
        super().__init__(f"desktop unavailable: {reason}")
        self.reason = reason


async def desktop_call(env: ToolEnv, path: str,
                       body: dict[str, Any] | None = None) -> dict[str, Any] | ToolResult:
    """Returns the worker's JSON, or a failed ToolResult for a worker-side
    error (e.g. unknown window). Raises DesktopUnavailable when locked/absent."""
    client = env.workers.get("desktop")
    if client is None:
        raise DesktopUnavailable("not_running")
    try:
        return await client.call("POST", path, task_id=env.task_id, json=body or {})
    except WorkerUnavailable:
        raise DesktopUnavailable("not_running") from None
    except WorkerDesktopLocked:
        raise DesktopUnavailable("locked") from None
    except WorkerError as e:
        return ToolResult(False, f"desktop worker: {e}")
