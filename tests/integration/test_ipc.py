"""Phase 5: IPC + worker skeleton — authenticated worker communication."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import shutil
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import httpx
import pytest

from core.ipc.client import (
    WorkerAuthError,
    WorkerClient,
    WorkerProtocolMismatch,
    WorkerUnavailable,
)
from core.ipc.monitor import WorkerMonitor
from core.ipc.server import make_worker_app, run_worker
from core.ipc.token import TokenStore
from core.log import setup_logging, shutdown_logging

APP_DIR = Path(__file__).resolve().parents[2]
CATS = ["core", "desktop", "browser", "provider", "tasks", "audit"]
V = 1


def load_worker_service(folder: str) -> ModuleType:
    path = APP_DIR / "workers" / folder / "service.py"
    spec = importlib.util.spec_from_file_location(f"svc_{folder.replace('-', '_')}", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def logs(tmp_path: Path) -> Iterator[Path]:
    d = tmp_path / "logs"
    setup_logging(d, CATS, worker="test", console=False)
    yield d
    shutdown_logging()


@pytest.fixture
def tokens(tmp_path: Path) -> TokenStore:
    t = TokenStore(tmp_path / "secrets")
    t.ensure()
    return t


def asgi_client(app, client_host: str = "127.0.0.1") -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(client_host, 5555)),
                             base_url="http://worker")


def headers(token: str, **over: str) -> dict[str, str]:
    h = {"Authorization": f"Bearer {token}", "X-Request-ID": uuid.uuid4().hex,
         "X-Task-ID": "12", "X-Protocol-Version": str(V)}
    h.update(over)
    return {k: v for k, v in h.items() if v != ""}


def desktop_app(tokens: TokenStore):  # type: ignore[no-untyped-def]
    app = make_worker_app("desktop", V, tokens, "desktop")
    load_worker_service("desktop-worker").register(app)
    return app


def get(app, path: str, hdrs: dict[str, str], host: str = "127.0.0.1") -> httpx.Response:  # type: ignore[no-untyped-def]
    async def go() -> httpx.Response:
        async with asgi_client(app, host) as c:
            return await c.get(path, headers=hdrs)
    return asyncio.run(go())


# ------------------------------------------------------------------ token

def test_token_file(tmp_path: Path) -> None:
    t = TokenStore(tmp_path)
    first = t.ensure()
    assert len(first) >= 40 and t.ensure() == first and t.matches(first)
    second = t.rotate()
    assert second != first and not t.matches(first) and t.matches(second)
    other_reader = TokenStore(tmp_path)
    assert other_reader.current() == second
    (tmp_path / "internal_rpc.token").write_text("short")
    with pytest.raises(ValueError):
        TokenStore(tmp_path).current()


# ------------------------------------------------------------- server guard

def test_authenticated_call_works(logs: Path, tokens: TokenStore) -> None:
    h = headers(tokens.current())
    r = get(desktop_app(tokens), "/v1/health", h)
    assert r.status_code == 200 and r.json()["worker"] == "desktop"
    assert r.json()["protocol_version"] == V
    assert r.headers["X-Request-ID"] == h["X-Request-ID"]
    line = json.loads((logs / "desktop" / "desktop.log").read_text("utf-8").splitlines()[-1])
    assert line["task_id"] == "12" and line["action"] == "rpc" and "duration" in line


@pytest.mark.parametrize("hdrs", [
    {"Authorization": ""},
    {"Authorization": "Bearer wrong-token-value-xxxxxxxxxxxxxxxxxxxxxxx"},
    {"Authorization": "Basic abc"},
])
def test_bad_token_rejected(logs: Path, tokens: TokenStore, hdrs: dict[str, str]) -> None:
    r = get(desktop_app(tokens), "/v1/health", headers(tokens.current(), **hdrs))
    assert r.status_code == 401 and r.json()["error"] == "UNAUTHORIZED"
    assert "protocol_version" not in r.text          # nothing leaks before auth
    audit = (logs / "audit" / "audit.log").read_text("utf-8")
    assert "rpc.reject" in audit and tokens.current() not in audit


@pytest.mark.parametrize("over", [
    {"X-Request-ID": ""}, {"X-Task-ID": ""}, {"X-Task-ID": "abc"},
    {"X-Request-ID": "short"}, {"X-Request-ID": "has spaces in it!"},
])
def test_missing_ids_rejected(logs: Path, tokens: TokenStore, over: dict[str, str]) -> None:
    r = get(desktop_app(tokens), "/v1/health", headers(tokens.current(), **over))
    assert r.status_code == 400 and r.json()["error"] == "BAD_REQUEST"


def test_system_task_id_allowed(logs: Path, tokens: TokenStore) -> None:
    r = get(desktop_app(tokens), "/v1/health", headers(tokens.current(), **{"X-Task-ID": "system"}))
    assert r.status_code == 200


def test_protocol_mismatch(logs: Path, tokens: TokenStore) -> None:
    r = get(desktop_app(tokens), "/v1/health",
            headers(tokens.current(), **{"X-Protocol-Version": "2"}))
    assert r.status_code == 409 and r.json()["error"] == "PROTOCOL_MISMATCH"


def test_non_loopback_client_refused(logs: Path, tokens: TokenStore) -> None:
    r = get(desktop_app(tokens), "/v1/health", headers(tokens.current()), host="192.168.1.50")
    assert r.status_code == 403 and r.json()["error"] == "NOT_LOOPBACK"


def test_bind_non_loopback_refused(tokens: TokenStore) -> None:
    with pytest.raises(ValueError, match="non-loopback"):
        run_worker(desktop_app(tokens), "0.0.0.0", 47999)


def test_desktop_session_info(logs: Path, tokens: TokenStore) -> None:
    body = get(desktop_app(tokens), "/v1/session", headers(tokens.current())).json()
    assert body["ok"] and body["user"]
    if sys.platform == "win32":
        assert isinstance(body["windows_session_id"], int)


def test_browser_sessions(logs: Path, tokens: TokenStore, tmp_path: Path) -> None:
    from tests.mocks.browser import browser_available, real_browser_client
    app = make_worker_app("browser", V, tokens, "browser")
    load_worker_service("browser-worker").register(app)

    async def go() -> None:
        async with asgi_client(app) as c:
            listed = (await c.get("/v1/sessions", headers=headers(tokens.current()))).json()
            assert listed == {"ok": True, "sessions": []}
            d = await c.delete("/v1/sessions/nope", headers=headers(tokens.current()))
            assert d.status_code == 404
            bad = await c.post("/v1/sessions", headers=headers(tokens.current()),
                               json={"url": "file:///C:/Windows/win.ini"})
            assert bad.status_code == 422 and "not allowed" in bad.json()["detail"]
    asyncio.run(go())
    if not browser_available():
        return
    # Session IDs are separate from task IDs (plan §17A).
    client, cli = real_browser_client(tmp_path)

    async def real() -> None:
        try:
            r = await client.call("POST", "/v1/sessions", task_id=12, json={})
            sid = r["session_id"]
            assert sid != "12" and cli.sessions[sid]["task_id"] == "12"
            listed = await client.call("GET", "/v1/sessions")
            assert [s["session_id"] for s in listed["sessions"]] == [sid]
            assert (await client.call("DELETE", f"/v1/sessions/{sid}"))["closed"] == sid
        finally:
            await cli.close_all()
            await client.aclose()
    asyncio.run(real())


# ------------------------------------------------------------------ client

def _client(app, tokens: TokenStore, version: int = V) -> WorkerClient:  # type: ignore[no-untyped-def]
    return WorkerClient("desktop", "127.0.0.1", 1, tokens, version,
                        client=httpx.AsyncClient(transport=httpx.ASGITransport(
                            app=app, client=("127.0.0.1", 5555))))


def test_client_follows_token_rotation(logs: Path, tmp_path: Path) -> None:
    worker_side = TokenStore(tmp_path / "secrets")
    worker_side.ensure()
    core_side = TokenStore(tmp_path / "secrets")
    app = desktop_app(worker_side)
    client = _client(app, core_side)
    assert asyncio.run(client.health())["ok"]
    time.sleep(0.02)
    worker_side.rotate()                              # e.g. scripts/rotate_rpc_token.py
    assert asyncio.run(client.health())["ok"]         # core re-reads the rotated file


def test_client_errors(logs: Path, tokens: TokenStore, tmp_path: Path) -> None:
    app = desktop_app(tokens)
    with pytest.raises(WorkerProtocolMismatch):
        asyncio.run(_client(app, tokens, version=99).health())
    stranger = TokenStore(tmp_path / "other")
    stranger.ensure()
    with pytest.raises(WorkerAuthError):
        asyncio.run(_client(app, stranger).health())
    dead = WorkerClient("desktop", "127.0.0.1", _free_port(), tokens, V, timeout=1)
    with pytest.raises(WorkerUnavailable):
        asyncio.run(dead.health())


def test_monitor_states_and_single_alert(logs: Path, tokens: TokenStore) -> None:
    good = _client(desktop_app(tokens), tokens)
    bad = _client(desktop_app(tokens), tokens, version=99)
    bad.name = "browser"
    mon = WorkerMonitor([good, bad])
    asyncio.run(mon.check_all())
    asyncio.run(mon.check_all())
    assert mon.probe("desktop")() [0] is True
    ok, detail = mon.probe("browser")()
    assert not ok and "PROTOCOL MISMATCH" in detail
    crit = [json.loads(x) for x in (logs / "core" / "core.log").read_text("utf-8").splitlines()]
    assert len([c for c in crit if c["level"] == "CRITICAL"]) == 1


# ------------------------------------------------------ real worker process

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
            return None if ip.startswith("127.") else str(ip)
    except OSError:
        return None


@pytest.mark.parametrize("folder", ["desktop-worker", "browser-worker"])
def test_real_worker_process(tmp_path: Path, folder: str) -> None:
    cfg = tmp_path / "config"
    shutil.copytree(APP_DIR / "config", cfg)
    root = tmp_path / "rt"
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, str(APP_DIR / "workers" / folder / "run.py"), "--port", str(port),
         "--config-dir", str(cfg), "--root", str(root)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    async def exercise() -> dict:  # type: ignore[type-arg]
        tokens = TokenStore(root / "secrets")
        client = WorkerClient(folder.split("-")[0], "127.0.0.1", port, tokens, V, timeout=2)
        try:
            deadline = time.time() + 20
            while True:
                try:
                    health = await client.health()
                    break
                except (WorkerUnavailable, FileNotFoundError, ValueError):
                    assert time.time() < deadline, "worker did not come up"
                    await asyncio.sleep(0.2)

            lan = _lan_ip()             # bound to loopback only: LAN address must refuse
            if lan:
                with socket.socket() as s, pytest.raises(OSError):
                    s.settimeout(1)
                    s.connect((lan, port))

            await client.call("POST", "/v1/shutdown")
            return health
        finally:
            await client.aclose()

    try:
        health = asyncio.run(exercise())
        # (pid differs from proc.pid: the venv python.exe on Windows is a launcher stub)
        assert health["ok"] and health["worker"] == folder.split("-")[0]
        assert proc.wait(timeout=15) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
