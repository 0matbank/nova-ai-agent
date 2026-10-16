"""Local dashboard API (Phase 24, plan §40).

Runs inside core on 127.0.0.1 only and sits on the SAME engines as Telegram: tasks go
through the task engine, approvals through the Approval Engine (single use, audited,
the broker still checks its own), /lockdown through SecurityCommands. It can never do
more than the owner's Telegram can (plan: "Dashboard নিজে privileged action bypass
করতে পারবে না").

Protection (a local web page is reachable by every website the owner visits):
- every /api call needs `Authorization: Bearer <token>` (secrets/dashboard.token) —
  a cross-site page cannot read it, so no CSRF
- the Host header must be 127.0.0.1/localhost:<port> — no DNS rebinding
- CSP self-only, no framing (clickjacking), no CORS, no-store
- secrets are never part of any response
"""

from __future__ import annotations

import dataclasses
import hmac
import secrets as pysecrets
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from core.log import add_known_secrets, get_logger, redact

_log = get_logger("core")
TOKEN_FILE = "dashboard.token"     # noqa: S105 - a file name, not a secret
CSP = ("default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")


def dashboard_token(secrets_dir: Path) -> str:
    path = secrets_dir / TOKEN_FILE
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(pysecrets.token_urlsafe(32), encoding="utf-8")
    token = path.read_text(encoding="utf-8").strip()
    add_known_secrets([token])
    return token


def jsonable(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, datetime):
        return (obj if obj.tzinfo else obj.replace(tzinfo=UTC)).isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, Path):
        return str(obj)
    return obj


@dataclasses.dataclass
class DashboardDeps:
    """Everything the dashboard reads or acts through (all optional: safe mode)."""
    cfg: Any                                   # AppConfig
    token: str
    owner_chat: str
    version: str = ""
    started_at: float = dataclasses.field(default_factory=time.time)
    store: Any = None                          # TaskStore
    engine: Any = None                         # TaskEngine
    approvals: Any = None                      # ApprovalManager
    sessions: Any = None                       # sessionmaker (audit log, approvals history)
    security: Any = None                       # SecurityCommands
    probes: dict[str, Callable[[], tuple[bool, str]]] = dataclasses.field(default_factory=dict)
    router: Any = None                         # ProviderRouter
    roster: Any = None                         # Roster
    registry: Any = None                       # SkillRegistry
    memory: Any = None                         # ProjectMemory
    schedules: Any = None                      # ScheduleStore
    ollama_ps: Callable[[], Awaitable[list[dict[str, Any]]]] | None = None


class NewTask(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class Decision(BaseModel):
    approve: bool


class Lockdown(BaseModel):
    on: bool


def create_dashboard_app(d: DashboardDeps, port: int, static_dir: Path | None) -> FastAPI:
    app = FastAPI(title="Nova AI dashboard", docs_url=None, redoc_url=None, openapi_url=None)
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]
                    ) -> Response:
        if request.headers.get("host", "") not in allowed_hosts:
            return JSONResponse({"error": "bad host"}, status_code=403)   # DNS rebinding
        if request.url.path.startswith("/api/"):
            auth = request.headers.get("authorization", "")
            given = auth[7:] if auth.lower().startswith("bearer ") else ""
            if not given or not hmac.compare_digest(given.encode(), d.token.encode()):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def need(obj: Any, what: str) -> Any:
        if obj is None:
            raise HTTPException(503, f"{what} unavailable (safe mode)")
        return obj

    # ------------------------------------------------------------- read

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        from core.health import drive_usage, pc_status
        probes = []
        for name, probe in d.probes.items():
            try:
                ok, detail = probe()
            except Exception as e:  # noqa: BLE001 - a broken probe must not break the page
                ok, detail = False, f"probe error: {type(e).__name__}"
            probes.append({"name": name, "ok": ok, "detail": detail})
        pc = await pc_status(d.cfg.root)
        store = d.store
        return {
            "agent": d.cfg.default.agent.name, "version": d.version[:8],
            "uptime_seconds": int(time.time() - d.started_at),
            "health": "OK" if all(p["ok"] for p in probes) else "DEGRADED",
            "probes": probes, "pc": jsonable(pc), "drives": drive_usage(),
            "queue_paused": bool(store and store.is_queue_paused()),
            "lockdown": bool(d.security and d.security.permissions.is_lockdown()),
            "running_task": getattr(d.engine, "current_task_id", None),
        }

    @app.get("/api/tasks")
    async def tasks(scope: str = "recent", limit: int = 30) -> list[dict[str, Any]]:
        from core.queue.states import TERMINAL, TaskState
        store = need(d.store, "database")
        rows = (store.list_in([s for s in TaskState if s not in TERMINAL]) if scope == "active"
                else store.list_recent(max(1, min(limit, 200))))
        return [_task(t, steps=False) for t in rows]

    @app.get("/api/tasks/{task_id}")
    async def task(task_id: int) -> dict[str, Any]:
        t = need(d.store, "database").get(task_id)
        if t is None:
            raise HTTPException(404, "no such task")
        return _task(t, steps=True)

    @app.get("/api/approvals")
    async def approvals() -> dict[str, Any]:
        from sqlalchemy import select

        from core.db.models import Approval
        pending = [_approval(a) for a in need(d.approvals, "approvals").pending()]
        recent = []
        if d.sessions is not None:
            with d.sessions() as s:
                for a in s.scalars(select(Approval).order_by(Approval.id.desc()).limit(30)):
                    det = a.details or {}
                    recent.append({"id": a.id, "task_id": a.task_id, "action": a.action,
                                   "level": a.level, "status": a.status,
                                   "target": det.get("target", ""),
                                   "summary": redact(str(det.get("summary", "")))[:500],
                                   "requested_at": jsonable(a.requested_at),
                                   "decided_at": jsonable(a.decided_at),
                                   "decided_by": a.decided_by})
        return {"pending": pending, "recent": recent}

    @app.get("/api/providers")
    async def providers() -> list[dict[str, Any]]:
        r = need(d.router, "provider router")
        out = []
        for name, a in r.adapters.items():
            out.append({"name": name, "state": a.health.state.value, "detail": a.health.detail,
                        "checked_at": a.health.checked_at,
                        "routable": r.capabilities.routable(name),
                        "circuit_open_seconds": int(r.breaker.open_for(name)),
                        "circuit_reason": r.breaker.reason(name)})
        return out

    @app.get("/api/models")
    async def models() -> dict[str, Any]:
        loaded: list[dict[str, Any]] = []
        if d.ollama_ps is not None:
            try:
                loaded = await d.ollama_ps()
            except Exception as e:  # noqa: BLE001 - Ollama may be off
                loaded = [{"error": type(e).__name__}]
        m = d.cfg.models
        return {"alias_sets": jsonable(m.alias_sets), "ollama_policy": jsonable(
            m.ollama_policy.model_dump()), "loaded": loaded}

    @app.get("/api/agents")
    async def agents() -> list[dict[str, Any]]:
        out = []
        for name, a in d.cfg.agents.agents.items():
            title = d.roster.title(name) if d.roster is not None else name
            out.append({"name": name, "title": title, "enabled": a.enabled,
                        "task_types": list(a.task_types), "skills": list(a.skills),
                        "provider_task_type": a.provider_task_type})
        for key, p in d.cfg.projects.items():
            out.append({"name": f"project:{key}", "title": p.name, "enabled": True,
                        "task_types": ["project"], "skills": [],
                        "provider_task_type": "coding", "specialist": p.specialist_role})
        return out

    @app.get("/api/skills")
    async def skills() -> list[dict[str, Any]]:
        reg = need(d.registry, "skills")
        levels = d.cfg.permissions.actions
        out = []
        for name, sk in sorted(reg.skills.items()):
            tools = []
            for tool, actions in sorted(sk.declared.items()):
                tools.append({"tool": tool, "actions": [
                    {"action": a, "level": getattr(levels.get(a), "value", "RED")}
                    for a in actions]})
            out.append({"name": name, "description": sk.manifest.description,
                        "tools": tools})
        return out

    @app.get("/api/memory")
    async def memory(project: str | None = None) -> dict[str, Any]:
        mem = need(d.memory, "project memory")
        projects = {k: p.name for k, p in d.cfg.projects.items()}
        keys = [project] if project else list(projects)
        entries = {k: [jsonable(e) for e in mem.entries(k)] for k in keys if k in projects}
        return {"projects": projects, "entries": entries}

    @app.get("/api/logs")
    async def logs(limit: int = 60) -> list[dict[str, Any]]:
        from sqlalchemy import select

        from core.db.models import AuditLog
        sessions = need(d.sessions, "database")
        with sessions() as s:
            rows = list(s.scalars(select(AuditLog).order_by(AuditLog.id.desc())
                                  .limit(max(1, min(limit, 500)))))
        return [{"id": r.id, "ts": jsonable(r.ts), "actor": r.actor, "action": r.action,
                 "target": redact(r.target or ""), "task_id": r.task_id, "status": r.status}
                for r in rows]

    @app.get("/api/schedules")
    async def schedules() -> list[dict[str, Any]]:
        return [jsonable(s) for s in need(d.schedules, "scheduler").active()]

    @app.get("/api/settings")
    async def settings() -> dict[str, Any]:
        c = d.cfg
        w = c.workers.workers
        return {
            "agent": jsonable(c.default.agent.model_dump()),
            "task_engine": jsonable(c.default.task_engine.model_dump()),
            "shutdown": jsonable(c.default.shutdown.model_dump()),
            "update": jsonable(c.default.update.model_dump()),
            "voice": {"tts_engine": c.default.voice.tts.engine,
                      "tts_enabled": c.default.voice.tts.enabled},
            "approval_timeout_seconds": c.permissions.approval.timeout_seconds,
            "workers": {"desktop": f"{w.desktop_worker.host}:{w.desktop_worker.port}",
                        "browser": f"{w.browser_worker.host}:{w.browser_worker.port}",
                        "broker": w.privileged_broker.enabled,
                        "broker_services": list(w.privileged_broker.services)},
            "whatsapp_enabled": c.default.whatsapp.enabled,
            "dashboard": jsonable(c.default.dashboard.model_dump()),
            "note": "Read-only here: settings live in config/*.yaml (secrets only in "
                    "secrets/.env and never shown).",
        }

    # ------------------------------------------------------------ actions
    # Same engines as Telegram — nothing here can skip a permission or an approval.

    @app.post("/api/tasks")
    async def new_task(body: NewTask) -> dict[str, Any]:
        from core.orchestrator.task_commands import USER_REQUEST
        store = need(d.store, "database")
        if d.security is not None and d.security.permissions.is_lockdown():
            raise HTTPException(423, "LOCKDOWN চালু — নতুন task নেওয়া হচ্ছে না")
        text = redact(body.text.strip())
        t = store.create(title=" ".join(text.split())[:60], request_text=text,
                         task_type=USER_REQUEST, channel="dashboard", chat_id=d.owner_chat)
        if d.engine is not None:
            d.engine.wake()
        _log.info(f"dashboard: task #{t.id} created", extra={"action": "dashboard.task",
                                                             "task_id": t.id})
        return _task(t, steps=False)

    @app.post("/api/tasks/{task_id}/cancel")
    async def cancel(task_id: int) -> dict[str, Any]:
        out = need(d.store, "database").request_cancel(task_id)
        if d.engine is not None:
            d.engine.wake()
        return {"outcome": jsonable(out)}

    @app.post("/api/queue/{op}")
    async def queue(op: str) -> dict[str, Any]:
        store = need(d.store, "database")
        if op == "pause":
            return {"paused": True, "tasks": store.pause_queue()}
        if op == "resume":
            ids = store.resume_queue()
            if d.engine is not None:
                d.engine.wake()
            return {"paused": False, "tasks": ids}
        raise HTTPException(404, "op must be pause or resume")

    @app.post("/api/approvals/{approval_id}")
    async def decide(approval_id: int, body: Decision) -> dict[str, Any]:
        mgr = need(d.approvals, "approvals")
        view = next((a for a in mgr.pending() if a.id == approval_id), None)
        if view is None:
            raise HTTPException(404, "no pending approval with this id")
        outcome = mgr.decide(view.nonce, body.approve, decided_by="dashboard")
        _log.info(f"dashboard: approval #{approval_id} {outcome}",
                  extra={"action": "dashboard.approval", "status": str(outcome)})
        return {"outcome": jsonable(outcome)}

    @app.post("/api/schedules/{schedule_id}/cancel")
    async def cancel_schedule(schedule_id: int) -> dict[str, Any]:
        return {"cancelled": need(d.schedules, "scheduler").cancel(schedule_id)}

    @app.post("/api/lockdown")
    async def lockdown(body: Lockdown) -> dict[str, Any]:
        from channels.base import IncomingMessage
        sec = need(d.security, "security")
        msg = IncomingMessage(channel="dashboard", chat_id=d.owner_chat, user_id="dashboard",
                              message_id="0", text="/lockdown", received_at=datetime.now(UTC))
        text = await sec.lockdown(msg, [] if body.on else ["off"])
        return {"lockdown": body.on, "message": text}

    # ------------------------------------------------------------- page

    if static_dir is not None and (static_dir / "index.html").is_file():
        root = static_dir.resolve()

        @app.get("/{path:path}", include_in_schema=False)
        async def page(path: str) -> FileResponse:
            target = (root / path).resolve() if path else root / "index.html"
            if not target.is_file() or root not in target.parents and target != root:
                target = root / "index.html"                  # SPA routes
            return FileResponse(target)

    return app


def _task(t: Any, steps: bool) -> dict[str, Any]:
    out = {"id": t.id, "title": redact(t.title), "state": t.state.value,
           "task_type": t.task_type, "channel": t.channel,
           "result_summary": redact(t.result_summary or ""),
           "error_code": t.error_code, "error_message": redact(t.error_message or ""),
           "retry_count": t.retry_count, "created_at": jsonable(t.created_at),
           "updated_at": jsonable(t.updated_at), "finished_at": jsonable(t.finished_at)}
    if steps:
        out["request_text"] = redact(t.request_text)
        out["steps"] = [{"seq": s.seq, "title": s.title, "status": s.status.value,
                         "error": redact(s.error or "")} for s in t.steps]
    return out


def _approval(a: Any) -> dict[str, Any]:
    return {"id": a.id, "task_id": a.task_id, "action": a.action, "level": a.level,
            "status": a.status.value, "target": a.target, "summary": redact(a.summary)}


async def ollama_loaded() -> list[dict[str, Any]]:
    """Models Ollama has in memory right now (read-only)."""
    import httpx

    from providers.ollama_local.adapter import DEFAULT_URL
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.get(f"{DEFAULT_URL}/api/ps")
        r.raise_for_status()
    return [{"name": m.get("name"), "size_gb": round((m.get("size") or 0) / 2**30, 2),
             "vram_gb": round((m.get("size_vram") or 0) / 2**30, 2),
             "expires_at": m.get("expires_at")} for m in r.json().get("models", [])]


def start_dashboard(deps: DashboardDeps, cfg: Any, stop: Any
                    ) -> tuple[list[Any], Callable[[], tuple[bool, str]]]:
    """Serve the dashboard inside core's event loop (127.0.0.1 only). It can never take
    core down: a busy port only shows up on its own /status line."""
    import asyncio

    import uvicorn
    dcfg = cfg.default.dashboard
    state: dict[str, Any] = {"ok": False, "detail": "off (config dashboard.enabled)"}
    if not dcfg.enabled:
        return [], lambda: (True, str(state["detail"]))
    static = cfg.app_dir / "dashboard" / "dist"
    app = create_dashboard_app(deps, dcfg.port, static)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=dcfg.port,
                                           log_level="warning", access_log=False,
                                           log_config=None))
    state["detail"] = "starting"

    async def serve() -> None:
        try:
            await server.serve()
        except (SystemExit, OSError) as e:      # uvicorn exits the process on a busy port
            state.update(ok=False, detail=f"not running — port {dcfg.port} busy?")
            _log.error(f"dashboard could not start on 127.0.0.1:{dcfg.port}: "
                       f"{type(e).__name__}", extra={"action": "dashboard.start",
                                                     "status": "error"})

    async def watch() -> None:
        while not server.started and not stop.is_set() and state["detail"] == "starting":
            await asyncio.sleep(0.2)
        if server.started:
            state.update(ok=True, detail=f"http://127.0.0.1:{dcfg.port}")
            _log.info(f"dashboard on http://127.0.0.1:{dcfg.port}",
                      extra={"action": "dashboard.start", "status": "ok"})
        await stop.wait()
        server.should_exit = True
    return ([asyncio.create_task(serve()), asyncio.create_task(watch())],
            lambda: (bool(state["ok"]), str(state["detail"])))
