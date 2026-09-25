"""Browser Worker for tests: the REAL worker app in-process (ASGI, real
Playwright CLI + headless Chromium), or a scriptable fake."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx

from core.ipc.client import WorkerClient
from core.ipc.server import make_worker_app
from core.ipc.token import TokenStore
from tests.mocks.desktop import FakeDesktop

APP_DIR = Path(__file__).resolve().parents[2]
WORKER = APP_DIR / "workers" / "browser-worker"
CLI_JS = WORKER / "node_modules" / "@playwright" / "cli" / "playwright-cli.js"


def browser_available() -> bool:
    node = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
    return CLI_JS.exists() and Path(node).exists()


def real_browser_client(tmp_path: Path) -> tuple[WorkerClient, Any]:
    spec = importlib.util.spec_from_file_location(
        f"svc_browser_{tmp_path.name}", WORKER / "service.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    dirs = {"workspace_dir": tmp_path / "ws", "sessions_dir": tmp_path / "sess"}
    ctx = SimpleNamespace(config=SimpleNamespace(root=tmp_path, path=lambda k: dirs[k]))
    tokens = TokenStore(tmp_path / "tok")
    tokens.ensure()
    app = make_worker_app("browser", 1, tokens, "browser")
    mod.register(app, ctx)
    client = WorkerClient("browser", "127.0.0.1", 1, tokens, 1, client=httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 5555)), timeout=120))
    return client, app.state.browser


class FakeBrowser(FakeDesktop):
    name = "browser"
