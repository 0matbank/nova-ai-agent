"""FastAPI factory for loopback worker RPC servers (plan §17A).

Checks, in order (an unauthenticated caller learns nothing beyond "401"):
  1. caller is on a loopback address          → 403 NOT_LOOPBACK
  2. valid bearer token                        → 401 UNAUTHORIZED
  3. X-Request-ID + X-Task-ID present/valid    → 400 BAD_REQUEST
  4. X-Protocol-Version equals ours            → 409 PROTOCOL_MISMATCH
"""

from __future__ import annotations

import ipaddress
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from core.ipc.protocol import (
    HEADER_PROTOCOL,
    HEADER_REQUEST_ID,
    HEADER_TASK_ID,
    RpcError,
    valid_request_id,
    valid_task_id,
)
from core.ipc.token import TokenStore
from core.log import get_logger

_audit = get_logger("audit")


def _error(status: int, code: RpcError, detail: str = "") -> JSONResponse:
    return JSONResponse({"ok": False, "error": code, "detail": detail}, status_code=status)


def _is_loopback(host: str | None) -> bool:
    try:
        return bool(host) and ipaddress.ip_address(str(host)).is_loopback
    except ValueError:
        return False


def make_worker_app(worker: str, protocol_version: int, tokens: TokenStore,
                    log_category: str) -> FastAPI:
    app = FastAPI(title=f"nova-{worker}", docs_url=None, redoc_url=None, openapi_url=None)
    log = get_logger(log_category)
    started = time.time()
    app.state.server = None

    @app.middleware("http")
    async def guard(request: Request,
                    call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        client = request.client.host if request.client else None
        if not _is_loopback(client):
            _audit.warning(f"{worker}: non-loopback RPC refused",
                           extra={"action": "rpc.reject", "status": "not_loopback"})
            return _error(403, RpcError.NOT_LOOPBACK)
        auth = request.headers.get("authorization", "")
        scheme, _, presented = auth.partition(" ")
        if scheme.lower() != "bearer" or not presented or not tokens.matches(presented):
            _audit.warning(f"{worker}: RPC with missing/invalid token refused",
                           extra={"action": "rpc.reject", "status": "unauthorized"})
            return _error(401, RpcError.UNAUTHORIZED)
        rid = request.headers.get(HEADER_REQUEST_ID)
        tid = request.headers.get(HEADER_TASK_ID)
        if not valid_request_id(rid) or not valid_task_id(tid):
            return _error(400, RpcError.BAD_REQUEST,
                          f"{HEADER_REQUEST_ID} and {HEADER_TASK_ID} are required")
        theirs = request.headers.get(HEADER_PROTOCOL)
        if theirs != str(protocol_version):
            return _error(409, RpcError.PROTOCOL_MISMATCH,
                          f"worker speaks v{protocol_version}, caller sent v{theirs}")
        t0 = time.perf_counter()
        response = await call_next(request)
        log.info(f"{request.method} {request.url.path} -> {response.status_code}",
                 extra={"worker": worker, "action": "rpc", "status": response.status_code,
                        "duration": round(time.perf_counter() - t0, 4),
                        "task_id": None if tid == "system" else tid})
        response.headers[HEADER_REQUEST_ID] = str(rid)
        return response

    @app.get("/v1/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "worker": worker, "protocol_version": protocol_version,
                "pid": os.getpid(), "uptime_seconds": round(time.time() - started, 1)}

    @app.post("/v1/shutdown")
    async def shutdown() -> dict[str, Any]:
        """Graceful stop signal (used by the core's shutdown procedure, plan §32A)."""
        server = app.state.server
        if server is not None:
            server.should_exit = True
        log.info(f"{worker}: graceful shutdown requested", extra={"action": "shutdown"})
        return {"ok": True, "stopping": server is not None}

    return app


def run_worker(app: FastAPI, host: str, port: int) -> None:
    if not _is_loopback(host):
        raise ValueError(f"refusing to bind worker RPC to non-loopback host {host!r}")
    config = uvicorn.Config(app, host=host, port=port, log_config=None, access_log=False,
                            log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    app.state.server = server
    server.run()
