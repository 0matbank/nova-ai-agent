"""Browser Worker skeleton (plan §4, §17A). Each browser session gets its own
ID, separate from task IDs. Playwright is attached in Phase 10."""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from core.ipc.protocol import HEADER_TASK_ID


def register(app: FastAPI, ctx: object | None = None) -> None:
    sessions: dict[str, dict[str, Any]] = {}

    @app.post("/v1/sessions")
    async def create_session(request: Request) -> dict[str, Any]:
        sid = uuid.uuid4().hex
        sessions[sid] = {"session_id": sid, "task_id": request.headers.get(HEADER_TASK_ID),
                         "created_at": time.time(), "engine": None}
        return {"ok": True, **sessions[sid]}

    @app.get("/v1/sessions")
    async def list_sessions() -> dict[str, Any]:
        return {"ok": True, "sessions": list(sessions.values())}

    @app.delete("/v1/sessions/{session_id}")
    async def close_session(session_id: str) -> dict[str, Any]:
        if sessions.pop(session_id, None) is None:
            raise HTTPException(404, "unknown browser session")
        return {"ok": True, "closed": session_id}
