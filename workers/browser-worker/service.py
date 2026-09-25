"""Browser Worker (plan §4, §15 Layer 1, §17A): Playwright CLI sessions with
their own IDs and the agent's own persistent profiles. Permission decisions
are made by the core; this worker only executes."""

from __future__ import annotations

import base64
import contextlib
import json
import re
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

sys.path.insert(0, str(Path(__file__).resolve().parent))
from browser_cli import BrowserError, PlaywrightCLI  # noqa: E402

from core.ipc.protocol import HEADER_TASK_ID  # noqa: E402


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OpenRequest(Strict):
    url: str | None = None
    profile: str | None = "default"   # None = throwaway in-memory profile
    headed: bool = False
    device: str | None = None       # "mobile" or a Playwright device name


class GotoRequest(Strict):
    url: str


class TargetRequest(Strict):
    ref: str


class FillRequest(Strict):
    ref: str
    text: str
    submit: bool = False


class KeyRequest(Strict):
    key: str


class FindRequest(Strict):
    text: str


def _parsed(value: Any) -> Any:
    """The CLI returns eval results as JSON text ('{"tag": ...}')."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _err(e: Exception, status: int = 422) -> JSONResponse:
    return JSONResponse({"ok": False, "error": "BROWSER_ERROR", "detail": str(e)},
                        status_code=status)


def register(app: FastAPI, ctx: Any = None) -> None:
    root = ctx.config.root if ctx is not None else Path.home() / "nova-browser"
    workdir = (ctx.config.path("workspace_dir") if ctx is not None else root) / "browser"
    profiles = (ctx.config.path("sessions_dir") if ctx is not None else root) / "browser"
    cli = PlaywrightCLI(workdir, profiles)
    app.state.browser = cli

    inner = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(a: Any) -> AsyncIterator[Any]:
        # Browsers must not outlive the worker (plan §17A cleanup).
        async with inner(a) as state:
            try:
                yield state
            finally:
                await cli.close_all()

    app.router.lifespan_context = lifespan

    async def info(sid: str, settle: bool = True) -> dict[str, Any]:
        # After an action, let dynamic pages finish before reporting; reads don't wait.
        settled = await cli.settle(sid) if settle else None
        return {"ok": True, "session_id": sid, "settled": settled,
                **(await cli.page_info(sid))}

    @app.post("/v1/sessions")
    async def open_session(req: OpenRequest, request: Request) -> Any:
        try:
            s, _ = await cli.open(req.url, req.profile, request.headers.get(HEADER_TASK_ID),
                                  req.headed, req.device)
            return {**(await info(s.id)), "profile": s.profile, "headed": s.headed}
        except BrowserError as e:
            return _err(e)

    @app.get("/v1/sessions")
    async def list_sessions() -> dict[str, Any]:
        return {"ok": True, "sessions": [
            {"session_id": s.id, "profile": s.profile, "task_id": s.task_id,
             "headed": s.headed} for s in cli.sessions.values()]}

    @app.delete("/v1/sessions/{sid}")
    async def close_session(sid: str) -> Any:
        try:
            await cli.close(sid)
            return {"ok": True, "closed": sid}
        except BrowserError as e:
            return _err(e, 404)

    @app.post("/v1/sessions/{sid}/goto")
    async def goto(sid: str, req: GotoRequest) -> Any:
        try:
            await cli.goto(sid, req.url)
            return await info(sid)
        except BrowserError as e:
            return _err(e)

    @app.post("/v1/sessions/{sid}/snapshot")
    async def snapshot(sid: str) -> Any:
        try:
            page = await info(sid, settle=False)
            data = await cli.command(sid, "snapshot")
            return {**page, "snapshot": data.get("snapshot", data)}
        except BrowserError as e:
            return _err(e)

    @app.post("/v1/sessions/{sid}/find")
    async def find(sid: str, req: FindRequest) -> Any:
        try:
            return {"ok": True, "matches": (await cli.command(sid, "find", req.text))}
        except BrowserError as e:
            return _err(e)

    @app.post("/v1/sessions/{sid}/text")
    async def text(sid: str) -> Any:
        try:
            page = await info(sid, settle=False)
            return {**page, "text": await cli.text(sid)}
        except BrowserError as e:
            return _err(e)

    @app.post("/v1/sessions/{sid}/describe")
    async def describe(sid: str, req: TargetRequest) -> Any:
        """What an element is (for the core's permission decision)."""
        try:
            js = ("el => ({tag: el.tagName, type: el.type || null, role: el.getAttribute('role'),"
                  " name: el.getAttribute('name'), placeholder: el.getAttribute('placeholder'),"
                  " text: (el.innerText || el.getAttribute('aria-label') || el.value || el.title"
                  " || '').slice(0,200)})")
            data = await cli.command(sid, "eval", js, req.ref)
            return {"ok": True, "element": _parsed(data.get("result"))}
        except BrowserError as e:
            return _err(e)

    @app.post("/v1/sessions/{sid}/click")
    async def click(sid: str, req: TargetRequest) -> Any:
        try:
            await cli.command(sid, "click", req.ref)
            return await info(sid)
        except BrowserError as e:
            return _err(e)

    @app.post("/v1/sessions/{sid}/fill")
    async def fill(sid: str, req: FillRequest) -> Any:
        try:
            await cli.command(sid, "fill", req.ref, req.text, *(["--submit"] if req.submit else []))
            return await info(sid)
        except BrowserError as e:
            return _err(e)

    @app.post("/v1/sessions/{sid}/press")
    async def press(sid: str, req: KeyRequest) -> Any:
        try:
            await cli.command(sid, "press", req.key)
            return await info(sid)
        except BrowserError as e:
            return _err(e)

    @app.post("/v1/sessions/{sid}/screenshot")
    async def screenshot(sid: str) -> Any:
        try:
            data = await cli.command(sid, "screenshot")
            # CLI answers with markdown: "- [Screenshot of viewport](.playwright-cli\page-….png)"
            m = re.search(r"\(([^)]+\.png)\)", str(data.get("result", "")))
            path = Path(m.group(1)) if m else Path()
            if not path.is_absolute():
                path = workdir / path
            if not path.exists():
                return _err(BrowserError("screenshot file not produced"))
            png = path.read_bytes()
            path.unlink(missing_ok=True)          # page captures may hold private data
            return {"ok": True, "png_b64": base64.b64encode(png).decode(),
                    **(await info(sid, settle=False))}
        except BrowserError as e:
            return _err(e)
