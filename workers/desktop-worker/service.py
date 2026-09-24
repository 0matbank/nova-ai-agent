"""Desktop Worker (plan §4): runs inside the Windows user session.
Apps (Phase 6), screenshots, UI Automation and clipboard (Phase 7)."""

from __future__ import annotations

import asyncio
import getpass
import os
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

sys.path.insert(0, str(Path(__file__).resolve().parent))
import desktop_apps  # noqa: E402
import desktop_clipboard  # noqa: E402
import desktop_screen  # noqa: E402
import desktop_ui  # noqa: E402

from core.bootstrap import AppContext  # noqa: E402


def windows_session_id() -> int | None:
    if sys.platform != "win32":
        return None
    import win32api
    import win32ts

    return int(win32ts.ProcessIdToSessionId(win32api.GetCurrentProcessId()))


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AppRequest(Strict):
    app: str
    args: list[str] = []


class ShotRequest(Strict):
    monitor: int = 0
    preview_width: int = 1280


class Target(Strict):
    window: str
    name: str | None = None
    automation_id: str | None = None
    control_type: str | None = None


class InspectRequest(Strict):
    window: str
    max_depth: int = 6
    limit: int = 300


class TypeRequest(Target):
    text: str
    replace: bool = False


class KeysRequest(Strict):
    window: str
    keys: str


class XYRequest(Strict):
    x: int
    y: int


class ClipboardSet(Strict):
    text: str


async def _call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        result: dict[str, Any] = await asyncio.to_thread(fn, *args, **kwargs)
    except desktop_screen.DesktopLocked:
        return JSONResponse({"ok": False, "error": "DESKTOP_LOCKED"}, status_code=423)
    except (desktop_apps.AppError, desktop_ui.UIError, ValueError) as e:
        raise HTTPException(422, str(e)) from None
    return {"ok": True, **result}


def register(app: FastAPI, ctx: AppContext | None = None) -> None:
    shots_dir = (ctx.config.path("workspace_dir") if ctx else Path.home() / "nova-shots") \
        / "screenshots"

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
            "locked": await asyncio.to_thread(desktop_screen.is_locked),
        }

    # ---------------------------------------------------------------- apps
    @app.post("/v1/apps/open")
    async def open_app(req: AppRequest) -> Any:
        return await _call(desktop_apps.open_app, req.app, req.args)

    @app.post("/v1/apps/close")
    async def close_app(req: AppRequest) -> Any:
        return await _call(desktop_apps.close_app, req.app)

    @app.post("/v1/apps/kill")
    async def kill_app(req: AppRequest) -> Any:
        return await _call(desktop_apps.kill_app, req.app)

    # ---------------------------------------------------------- screenshot
    @app.post("/v1/screenshot")
    async def screenshot(req: ShotRequest) -> Any:
        return await _call(desktop_screen.capture, shots_dir, req.monitor, req.preview_width)

    # ---------------------------------------------------------------- UIA
    @app.post("/v1/ui/windows")
    async def windows() -> Any:
        return await _call(lambda: {"windows": desktop_ui.list_windows()})

    @app.post("/v1/ui/resolve")
    async def resolve(t: Target) -> Any:
        return await _call(desktop_ui.resolve, t.window, t.name, t.automation_id,
                           t.control_type)

    @app.post("/v1/ui/inspect")
    async def inspect(r: InspectRequest) -> Any:
        return await _call(desktop_ui.inspect, r.window, r.max_depth, r.limit)

    @app.post("/v1/ui/focus")
    async def focus(t: Target) -> Any:
        return await _call(desktop_ui.focus, t.window)

    @app.post("/v1/ui/click")
    async def click(t: Target) -> Any:
        return await _call(desktop_ui.click, t.window, t.name, t.automation_id, t.control_type)

    @app.post("/v1/ui/type")
    async def type_text(r: TypeRequest) -> Any:
        return await _call(desktop_ui.type_text, r.window, r.text, r.name, r.automation_id,
                           r.control_type, replace=r.replace)

    @app.post("/v1/ui/keys")
    async def keys(r: KeysRequest) -> Any:
        return await _call(desktop_ui.send_keys, r.window, r.keys)

    @app.post("/v1/ui/value")
    async def value(t: Target) -> Any:
        return await _call(desktop_ui.read_value, t.window, t.name, t.automation_id,
                           t.control_type)

    @app.post("/v1/ui/click_xy")
    async def click_xy(r: XYRequest) -> Any:
        return await _call(desktop_ui.click_xy, r.x, r.y)

    # ----------------------------------------------------------- clipboard
    @app.post("/v1/clipboard/get")
    async def clip_get() -> Any:
        return await _call(desktop_clipboard.get_text)

    @app.post("/v1/clipboard/set")
    async def clip_set(r: ClipboardSet) -> Any:
        return await _call(desktop_clipboard.set_text, r.text)
