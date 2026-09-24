"""Desktop Worker (plan §4): runs inside the Windows user session.
Phase 6: app open/close/kill. Screenshot/UI Automation arrive in Phase 7."""

from __future__ import annotations

import asyncio
import getpass
import os
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict

sys.path.insert(0, str(Path(__file__).resolve().parent))
import desktop_apps  # noqa: E402


def windows_session_id() -> int | None:
    if sys.platform != "win32":
        return None
    import win32api
    import win32ts

    return int(win32ts.ProcessIdToSessionId(win32api.GetCurrentProcessId()))


class AppRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app: str
    args: list[str] = []


async def _call(fn: Any, *args: Any) -> dict[str, Any]:
    try:
        result: dict[str, Any] = await asyncio.to_thread(fn, *args)
    except desktop_apps.AppError as e:
        raise HTTPException(422, str(e)) from None
    return {"ok": True, **result}


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

    @app.post("/v1/apps/open")
    async def open_app(req: AppRequest) -> dict[str, Any]:
        return await _call(desktop_apps.open_app, req.app, req.args)

    @app.post("/v1/apps/close")
    async def close_app(req: AppRequest) -> dict[str, Any]:
        return await _call(desktop_apps.close_app, req.app)

    @app.post("/v1/apps/kill")
    async def kill_app(req: AppRequest) -> dict[str, Any]:
        return await _call(desktop_apps.kill_app, req.app)
