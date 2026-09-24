"""Desktop Worker for tests: the REAL worker app in-process (ASGI), or a
scriptable fake for lock / shell-window scenarios."""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from core.ipc.client import WorkerClient
from core.ipc.server import make_worker_app
from core.ipc.token import TokenStore

APP_DIR = Path(__file__).resolve().parents[2]


def real_desktop_client(tmp_path: Path) -> WorkerClient:
    spec = importlib.util.spec_from_file_location(
        f"svc_desktop_{tmp_path.name}", APP_DIR / "workers" / "desktop-worker" / "service.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    tokens = TokenStore(tmp_path / "tok")
    tokens.ensure()
    app = make_worker_app("desktop", 1, tokens, "desktop")
    mod.register(app)
    return WorkerClient("desktop", "127.0.0.1", 1, tokens, 1, client=httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 5555)), timeout=60))


class FakeDesktop:
    """Duck-typed WorkerClient. `routes` maps path → dict or callable(body) or Exception."""

    name = "desktop"

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    async def call(self, method: str, path: str, *, task_id: int | str = "system",
                   json: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((path, json))
        r = self.routes[path]
        if isinstance(r, Exception):
            raise r
        if isinstance(r, Callable):  # type: ignore[arg-type]
            return r(json)  # type: ignore[no-any-return]
        return dict(r)

    async def aclose(self) -> None:
        pass
