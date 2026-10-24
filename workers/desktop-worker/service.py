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
from pydantic import BaseModel, ConfigDict, Field

sys.path.insert(0, str(Path(__file__).resolve().parent))
import desktop_act  # noqa: E402
import desktop_apps  # noqa: E402
import desktop_clipboard  # noqa: E402
import desktop_device  # noqa: E402
import desktop_guard  # noqa: E402
import desktop_inside  # noqa: E402
import desktop_lock  # noqa: E402
import desktop_observe  # noqa: E402
import desktop_screen  # noqa: E402
import desktop_ui  # noqa: E402
import desktop_windows  # noqa: E402

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


class VolumeRequest(Strict):
    level: int | None = Field(default=None, ge=0, le=100)
    change: int | None = Field(default=None, ge=-100, le=100)
    mute: bool | None = None


class MediaRequest(Strict):
    action: str


class BrightnessRequest(Strict):
    level: int | None = Field(default=None, ge=0, le=100)
    change: int | None = Field(default=None, ge=-100, le=100)


class ObserveRequest(Strict):
    window: str | None = None          # title; none = the window in front
    hwnd: int | None = None
    image: bool = True
    focus: bool = False                # bring it to the front first (the work loop)


class ElementRef(Strict):
    observation_id: str
    element: int


class ActRequest(ElementRef):
    op: str = "click"
    text: str | None = None
    at: str = "end"                    # "type": append at the end, or at the cursor


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
    if ctx is not None:
        # V1.1: installed apps (written by the core) — names + what may be opened
        catalog = ctx.config.path("data_dir") / "app_catalog.json"
        desktop_windows.CATALOG_FILE = desktop_apps.CATALOG_FILE = catalog

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

    @app.post("/v1/device/volume")
    async def device_volume(r: VolumeRequest) -> Any:
        # V1.2: no AI — Core Audio in the owner's session; before/after is the proof
        if r.level is None and r.change is None and r.mute is None:
            return await _call(desktop_device.volume_state)
        return await _call(desktop_device.set_volume, r.level, r.change, r.mute)

    @app.post("/v1/device/media")
    async def device_media(r: MediaRequest) -> Any:
        return await _call(desktop_device.media, r.action)

    @app.post("/v1/device/brightness")
    async def device_brightness(r: BrightnessRequest) -> Any:
        return await _call(desktop_device.brightness, r.level, r.change)

    @app.post("/v1/apps/visible")
    async def visible_apps() -> Any:
        # V1.1: what is open on screen (windows grouped by app), not the process list
        return await _call(desktop_windows.visible_apps)

    @app.post("/v1/apps/view")
    async def view_app(req: AppRequest) -> Any:
        # what an open app's windows and pop-ups show (read-only)
        return await _call(desktop_inside.view, req.app)

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

    @app.post("/v1/ui/observe")
    async def observe(r: ObserveRequest) -> Any:
        # plan §71 CU-1: one window's controls + screenshot + numbered marks, any app
        return await _call(desktop_observe.observe, shots_dir.parent / "observe", r.window,
                           r.hwnd, r.image, r.focus)

    @app.get("/v1/ui/halt")
    async def halt_state() -> Any:
        # CU-3b: has the owner pressed Escape (Nova stopped)? is Escape watched?
        return {"ok": True, "halted": desktop_guard.halted(),
                "armed": desktop_guard.ESCAPE.armed(),
                "owner_active": desktop_guard.owner_active()}

    @app.post("/v1/ui/halt/clear")
    async def halt_clear() -> Any:
        desktop_guard.clear_halt()
        return {"ok": True, "halted": False}

    @app.post("/v1/ui/target")
    async def act_target(r: ElementRef) -> Any:
        # CU-3: what an action would hit, re-checked against the screen now
        return await _call(desktop_act.target, r.observation_id, r.element)

    @app.post("/v1/ui/act")
    async def act(r: ActRequest) -> Any:
        # CU-3: one element of one observation, proven unchanged right before the input
        return await _call(desktop_act.act, r.observation_id, r.element, r.op, r.text,
                           r.at)

    @app.post("/v1/ui/inspect")
    async def inspect(r: InspectRequest) -> Any:
        return await _call(desktop_ui.inspect, r.window, r.max_depth, r.limit)

    @app.post("/v1/ui/focus")
    async def focus(t: Target) -> Any:
        return await _call(desktop_lock.leased(desktop_ui.focus), t.window)

    @app.post("/v1/ui/click")
    async def click(t: Target) -> Any:
        return await _call(desktop_lock.leased(desktop_ui.click), t.window, t.name,
                           t.automation_id, t.control_type)

    @app.post("/v1/ui/type")
    async def type_text(r: TypeRequest) -> Any:
        return await _call(desktop_lock.leased(desktop_ui.type_text), r.window, r.text, r.name,
                           r.automation_id,
                           r.control_type, replace=r.replace)

    @app.post("/v1/ui/keys")
    async def keys(r: KeysRequest) -> Any:
        return await _call(desktop_lock.leased(desktop_ui.send_keys), r.window, r.keys)

    @app.post("/v1/ui/value")
    async def value(t: Target) -> Any:
        return await _call(desktop_ui.read_value, t.window, t.name, t.automation_id,
                           t.control_type)

    @app.post("/v1/ui/click_xy")
    async def click_xy(r: XYRequest) -> Any:
        return await _call(desktop_lock.leased(desktop_ui.click_xy), r.x, r.y)

    # ----------------------------------------------------------- clipboard
    @app.post("/v1/clipboard/get")
    async def clip_get() -> Any:
        return await _call(desktop_clipboard.get_text)

    @app.post("/v1/clipboard/set")
    async def clip_set(r: ClipboardSet) -> Any:
        return await _call(desktop_clipboard.set_text, r.text)
