"""Browser Worker (plan §4, §15, §17A): browser sessions with their own IDs and
the agent's own profiles, on one of two engines (see engines.py):

  engine="cli"  Playwright CLI — routine work (default)
  engine="mcp"  Playwright MCP — complex / multi-step work + vision coordinates

Permission decisions are made by the core; this worker only executes. After
every action the page address is checked again: a click must never land the
browser on this PC's own services (it is sent back and the action reported).
"""

# No `from __future__ import annotations` here: FastAPI/pydantic resolve the
# request models' annotations at runtime, also when tests load this file by path.
import contextlib
import sys
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

sys.path.insert(0, str(Path(__file__).resolve().parent))
from browser_cli import BrowserError, PlaywrightCLI, check_url  # noqa: E402
from engines import CLIEngine, MCPEngine, b64  # noqa: E402

from core.ipc.protocol import HEADER_TASK_ID  # noqa: E402


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OpenRequest(Strict):
    url: str | None = None
    profile: str | None = "default"   # None = throwaway in-memory profile
    headed: bool = False
    device: str | None = None       # "mobile" or a Playwright device name
    engine: Literal["cli", "mcp"] = "cli"


class GotoRequest(Strict):
    url: str


class TargetRequest(Strict):
    ref: str


class FillRequest(Strict):
    ref: str
    text: str
    submit: bool = False


class SelectRequest(Strict):
    ref: str
    value: str


class KeyRequest(Strict):
    key: str


class FindRequest(Strict):
    text: str


class PointRequest(Strict):
    x: int
    y: int


def _err(e: Exception, status: int = 422) -> JSONResponse:
    return JSONResponse({"ok": False, "error": "BROWSER_ERROR", "detail": str(e)},
                        status_code=status)


def register(app: FastAPI, ctx: Any = None) -> None:
    root = ctx.config.root if ctx is not None else Path.home() / "nova-browser"
    workdir = (ctx.config.path("workspace_dir") if ctx is not None else root) / "browser"
    profiles = (ctx.config.path("sessions_dir") if ctx is not None else root) / "browser"
    cli = PlaywrightCLI(workdir, profiles)
    engines: dict[str, CLIEngine | MCPEngine] = {"cli": CLIEngine(cli),
                                                  "mcp": MCPEngine(workdir, profiles)}
    owner: dict[str, str] = {}                 # session id → engine name
    app.state.browser = cli
    app.state.engines = engines

    async def close_all() -> None:
        for sid in list(owner):
            with contextlib.suppress(Exception):
                await engines[owner.pop(sid)].close(sid)
        await cli.close_all()

    app.state.close_all = close_all
    inner = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(a: Any) -> AsyncIterator[Any]:
        # Browsers must not outlive the worker (plan §17A cleanup).
        async with inner(a) as state:
            try:
                yield state
            finally:
                await close_all()

    app.router.lifespan_context = lifespan

    def engine_of(sid: str) -> CLIEngine | MCPEngine:
        name = owner.get(sid)
        if name is None:
            raise BrowserError("unknown browser session")
        return engines[name]

    async def info(sid: str, settle: bool = True) -> dict[str, Any]:
        # After an action, let dynamic pages finish before reporting; reads don't wait.
        e = engine_of(sid)
        settled = await e.settle(sid) if settle else None
        return {"ok": True, "session_id": sid, "engine": e.name, "settled": settled,
                **(await e.page_info(sid))}

    async def act(sid: str, action: Callable[[], Awaitable[None]]) -> Any:
        """Run an action, then make sure the page did not end up somewhere forbidden."""
        try:
            await action()
            page = await info(sid)
            url = str(page.get("url", ""))
            if url and not url.startswith(("about:", "data:text/html")):
                try:
                    check_url(url)
                except BrowserError as blocked:
                    with contextlib.suppress(BrowserError):
                        await engine_of(sid).back(sid)
                    return _err(BrowserError(f"navigation undone — {blocked}"))
            return page
        except BrowserError as e:
            return _err(e)

    @app.post("/v1/sessions")
    async def open_session(req: OpenRequest, request: Request) -> Any:
        taken = {s["profile"] for e in engines.values() for s in e.sessions().values()}
        if req.profile is not None and req.profile in taken:
            return _err(BrowserError(f"profile {req.profile!r} is already open in another "
                                     "session"))
        engine = engines[req.engine]
        try:
            sid = await engine.open(uuid.uuid4().hex if req.engine == "mcp" else None,
                                    req.url, req.profile, request.headers.get(HEADER_TASK_ID),
                                    req.headed, req.device)
            owner[sid] = req.engine
            return {**(await info(sid)), "profile": req.profile, "headed": req.headed}
        except BrowserError as e:
            return _err(e)

    @app.get("/v1/sessions")
    async def list_sessions() -> dict[str, Any]:
        return {"ok": True, "sessions": [
            {"session_id": sid, "engine": e.name, **meta}
            for e in engines.values() for sid, meta in e.sessions().items()]}

    @app.delete("/v1/sessions/{sid}")
    async def close_session(sid: str) -> Any:
        try:
            await engine_of(sid).close(sid)
            owner.pop(sid, None)
            return {"ok": True, "closed": sid}
        except BrowserError as e:
            owner.pop(sid, None)
            return _err(e, 404)

    @app.post("/v1/sessions/{sid}/goto")
    async def goto(sid: str, req: GotoRequest) -> Any:
        return await act(sid, lambda: engine_of(sid).goto(sid, req.url))

    @app.post("/v1/sessions/{sid}/snapshot")
    async def snapshot(sid: str) -> Any:
        try:
            page = await info(sid, settle=False)
            e = engine_of(sid)
            return {**page, "format": "tree" if e.name == "cli" else "yaml",
                    "snapshot": await e.snapshot(sid)}
        except BrowserError as e:
            return _err(e)

    @app.post("/v1/sessions/{sid}/find")
    async def find(sid: str, req: FindRequest) -> Any:
        try:
            return {"ok": True, "matches": await engine_of(sid).find(sid, req.text)}
        except BrowserError as e:
            return _err(e)

    @app.post("/v1/sessions/{sid}/text")
    async def text(sid: str) -> Any:
        try:
            page = await info(sid, settle=False)
            return {**page, "text": await engine_of(sid).text(sid)}
        except BrowserError as e:
            return _err(e)

    @app.post("/v1/sessions/{sid}/describe")
    async def describe(sid: str, req: TargetRequest) -> Any:
        """What an element is (for the core's permission decision)."""
        try:
            return {"ok": True, "element": await engine_of(sid).describe(sid, req.ref)}
        except BrowserError as e:
            return _err(e)

    @app.post("/v1/sessions/{sid}/describe_xy")
    async def describe_xy(sid: str, req: PointRequest) -> Any:
        """What sits at a screen point (vision fallback's permission decision)."""
        try:
            return {"ok": True, "element": await engine_of(sid).describe_xy(sid, req.x, req.y)}
        except BrowserError as e:
            return _err(e)

    @app.post("/v1/sessions/{sid}/click")
    async def click(sid: str, req: TargetRequest) -> Any:
        return await act(sid, lambda: engine_of(sid).click(sid, req.ref))

    @app.post("/v1/sessions/{sid}/click_xy")
    async def click_xy(sid: str, req: PointRequest) -> Any:
        return await act(sid, lambda: engine_of(sid).click_xy(sid, req.x, req.y))

    @app.post("/v1/sessions/{sid}/fill")
    async def fill(sid: str, req: FillRequest) -> Any:
        return await act(sid, lambda: engine_of(sid).fill(sid, req.ref, req.text, req.submit))

    @app.post("/v1/sessions/{sid}/select")
    async def select(sid: str, req: SelectRequest) -> Any:
        return await act(sid, lambda: engine_of(sid).select(sid, req.ref, req.value))

    @app.post("/v1/sessions/{sid}/press")
    async def press(sid: str, req: KeyRequest) -> Any:
        return await act(sid, lambda: engine_of(sid).press(sid, req.key))

    @app.post("/v1/sessions/{sid}/back")
    async def back(sid: str) -> Any:
        return await act(sid, lambda: engine_of(sid).back(sid))

    @app.post("/v1/sessions/{sid}/screenshot")
    async def screenshot(sid: str) -> Any:
        try:
            png = await engine_of(sid).screenshot(sid)
            return {"ok": True, "png_b64": b64(png), **(await info(sid, settle=False))}
        except BrowserError as e:
            return _err(e)
