"""Desktop Worker skeleton (plan §4): runs inside the Windows user session.
Mouse/keyboard/window/screenshot/UI Automation arrive in Phases 6–7."""

from __future__ import annotations

import getpass
import os
import sys
from typing import Any

from fastapi import FastAPI


def windows_session_id() -> int | None:
    if sys.platform != "win32":
        return None
    import win32api
    import win32ts

    return int(win32ts.ProcessIdToSessionId(win32api.GetCurrentProcessId()))


def register(app: FastAPI) -> None:
    @app.get("/v1/session")
    async def session() -> dict[str, Any]:
        sid = windows_session_id()
        return {
            "ok": True,
            "user": getpass.getuser(),
            "pid": os.getpid(),
            "windows_session_id": sid,
            # Session 0 = services; a real desktop needs an interactive user session.
            "interactive_session": sid is not None and sid != 0,
        }
