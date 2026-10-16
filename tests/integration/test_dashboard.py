"""Phase 24: the local dashboard — local-only protection (token, Host check, headers),
the same engines as Telegram (approvals, lockdown, tasks), no secrets in responses."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.dashboard.api import DashboardDeps, create_dashboard_app
from core.queue.engine import ApprovalPending
from tests.mocks.skills import make_skill_env

PORT = 47810
TOKEN = "t" * 43
APP = Path(__file__).resolve().parents[2]


@pytest.fixture
def env(tmp_path: Path):  # type: ignore[no-untyped-def]
    s = make_skill_env(tmp_path)
    from core.orchestrator.security_commands import SecurityCommands
    sec = SecurityCommands(s.tasks.permissions, s.tasks.approvals, s.tasks.store, s.tasks.audit)
    deps = DashboardDeps(cfg=s.cfg, token=TOKEN, owner_chat="555", version="abc12345",
                         store=s.tasks.store, engine=s.tasks.engine, approvals=s.tasks.approvals,
                         sessions=s.tasks.store._sessions, security=sec,
                         probes={"Telegram": lambda: (True, "connected")},
                         registry=s.runner.registry)
    app = create_dashboard_app(deps, PORT, APP / "dashboard" / "dist")
    client = TestClient(app, base_url=f"http://127.0.0.1:{PORT}")
    return s, client


def auth(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_api_needs_the_token_and_a_local_host(env) -> None:  # type: ignore[no-untyped-def]
    _, c = env
    assert c.get("/api/status").status_code == 401
    assert c.get("/api/status", headers=auth("x" * 43)).status_code == 401
    r = c.get("/api/status", headers=auth())
    assert r.status_code == 200 and r.json()["probes"][0]["name"] == "Telegram"
    # DNS rebinding: a page on evil.example resolving to 127.0.0.1 sends its own Host
    bad = c.get("/api/status", headers={**auth(), "Host": "evil.example:47810"})
    assert bad.status_code == 403
    assert r.headers["X-Frame-Options"] == "DENY" and "frame-ancestors 'none'" in \
        r.headers["Content-Security-Policy"] and r.headers["Cache-Control"] == "no-store"
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


def test_page_is_served_without_data_and_no_file_escapes(env) -> None:  # type: ignore[no-untyped-def]
    _, c = env
    page = c.get("/")
    assert page.status_code == 200 and "<div id=\"root\">" in page.text
    assert "Content-Security-Policy" in page.headers
    for sneaky in ("/..%2f..%2fcore%2fservice.py", "/assets/..%2f..%2f..%2fpyproject.toml"):
        r = c.get(sneaky)
        assert "def " not in r.text and "[project]" not in r.text      # index.html instead


def test_task_from_the_dashboard_goes_through_the_engine(env) -> None:  # type: ignore[no-untyped-def]
    s, c = env
    r = c.post("/api/tasks", json={"text": "PC er obostha ki"}, headers=auth())
    assert r.status_code == 200
    t = s.tasks.store.get(r.json()["id"])
    assert t.channel == "dashboard" and t.chat_id == "555" and t.task_type == "user_request"
    listed = c.get("/api/tasks", headers=auth()).json()
    assert listed[0]["id"] == t.id
    detail = c.get(f"/api/tasks/{t.id}", headers=auth()).json()
    assert detail["request_text"] == "PC er obostha ki"
    assert c.post(f"/api/tasks/{t.id}/cancel", headers=auth()).status_code == 200


def test_approval_from_the_dashboard_is_the_same_single_use_approval(env) -> None:  # type: ignore[no-untyped-def]
    s, c = env
    tid = s.new_task()
    with pytest.raises(ApprovalPending):                 # a RED terminal command
        s.call("terminal", "run", {"program": "git", "args": ["init"]}, tid)
    pending = c.get("/api/approvals", headers=auth()).json()["pending"]
    assert len(pending) == 1 and pending[0]["level"] == "RED"
    a = pending[0]
    r = c.post(f"/api/approvals/{a['id']}", json={"approve": True}, headers=auth())
    assert r.json()["outcome"] == "approved"
    hist = c.get("/api/approvals", headers=auth()).json()
    assert hist["pending"] == [] and hist["recent"][0]["decided_by"] == "dashboard"
    # a decided approval cannot be decided again
    assert c.post(f"/api/approvals/{a['id']}", json={"approve": False},
                  headers=auth()).status_code == 404


def test_lockdown_from_the_dashboard_blocks_new_tasks(env) -> None:  # type: ignore[no-untyped-def]
    s, c = env
    r = c.post("/api/lockdown", json={"on": True}, headers=auth())
    assert r.status_code == 200 and "LOCKDOWN" in r.json()["message"]
    assert c.post("/api/tasks", json={"text": "x"}, headers=auth()).status_code == 423
    assert c.get("/api/status", headers=auth()).json()["lockdown"] is True
    c.post("/api/lockdown", json={"on": False}, headers=auth())
    assert c.post("/api/tasks", json={"text": "x"}, headers=auth()).status_code == 200


def test_read_views_and_no_secrets(env) -> None:  # type: ignore[no-untyped-def]
    s, c = env
    for path in ("skills", "agents", "settings", "logs", "status"):
        r = c.get(f"/api/{path}", headers=auth())
        assert r.status_code == 200, path
        assert TOKEN not in r.text
    skills = {x["name"]: x for x in c.get("/api/skills", headers=auth()).json()}
    levels = {a["action"]: a["level"] for t in skills["files"]["tools"] for a in t["actions"]}
    assert levels.get("file.read") == "GREEN"
    settings = c.get("/api/settings", headers=auth()).json()
    assert settings["dashboard"]["port"] == PORT and "secrets" in settings["note"]
    # safe mode: a missing part answers 503 instead of crashing
    deps = DashboardDeps(cfg=s.cfg, token=TOKEN, owner_chat="555")
    c2 = TestClient(create_dashboard_app(deps, PORT, None),
                    base_url=f"http://127.0.0.1:{PORT}")
    assert c2.get("/api/tasks", headers=auth()).status_code == 503


def test_built_page_matches_its_assets() -> None:
    dist = APP / "dashboard" / "dist"
    html = (dist / "index.html").read_text(encoding="utf-8")
    import re
    assets = re.findall(r'(?:src|href)="/(assets/[^"]+)"', html)
    assert assets and all((dist / a).is_file() for a in assets)
    assert "http" not in html.replace("http-equiv", "")          # nothing from the internet
    asyncio.run(asyncio.sleep(0))


def test_a_busy_port_never_takes_core_down(tmp_path: Path) -> None:
    """Live finding: uvicorn exits the whole process when its port is taken."""
    import socket

    from core.dashboard.api import start_dashboard
    s = make_skill_env(tmp_path)
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen()
    from core.config.schema import DashboardSection
    cfg = s.cfg.model_copy(update={"default": s.cfg.default.model_copy(update={
        "dashboard": DashboardSection(port=blocker.getsockname()[1])})})

    async def go() -> tuple[bool, str]:
        stop = asyncio.Event()
        tasks, probe = start_dashboard(DashboardDeps(cfg=cfg, token=TOKEN, owner_chat="5"),
                                       cfg, stop)
        for _ in range(50):
            await asyncio.sleep(0.1)
            if "busy" in probe()[1]:
                break
        result = probe()
        stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        return result
    try:
        ok, detail = asyncio.run(go())               # no SystemExit escapes
    finally:
        blocker.close()
    assert not ok and "busy" in detail


def test_the_page_reads_the_real_gpu_fields() -> None:
    """Live finding: the page asked for vram_used_gb (does not exist) → 'VRAM ?/? GB'."""
    import dataclasses

    from core.health import GpuStatus
    src = (APP / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
    import re
    used = set(re.findall(r"\bg\.([a-z_]+)", src))
    fields = {f.name for f in dataclasses.fields(GpuStatus)}
    assert used and used <= fields, used - fields
