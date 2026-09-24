from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import httpx
import psutil
import pytest

from core.ipc.client import WorkerClient
from core.ipc.server import make_worker_app
from core.ipc.token import TokenStore
from core.queue.engine import ApprovalPending
from core.skills.desktop import NEEDS_DESKTOP
from tests.mocks.skills import SkillEnv, make_skill_env

APP_DIR = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                reason="app-control is a Windows-only skill")


def desktop_client(tmp_path: Path) -> WorkerClient:
    spec = importlib.util.spec_from_file_location(
        "svc_desktop_for_apps", APP_DIR / "workers" / "desktop-worker" / "service.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    tokens = TokenStore(tmp_path / "tok")
    tokens.ensure()
    app = make_worker_app("desktop", 1, tokens, "desktop")
    mod.register(app)
    return WorkerClient("desktop", "127.0.0.1", 1, tokens, 1, client=httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 5555))))


def test_no_desktop_worker_reports_unlock_needed(tmp_path: Path) -> None:
    s = make_skill_env(tmp_path)
    r = s.call("app-control", "open", {"app": "notepad"})
    assert not r.ok and r.summary == NEEDS_DESKTOP and r.data["needs_desktop"]


@pytest.fixture
def s(tmp_path: Path) -> SkillEnv:
    return make_skill_env(tmp_path, {"desktop": desktop_client(tmp_path)})


def test_unknown_app_rejected(s: SkillEnv) -> None:
    r = s.call("app-control", "open", {"app": "totally-unknown-app"})
    assert not r.ok and "unknown app" in r.summary


def test_explorer_close_refused(s: SkillEnv) -> None:
    r = s.call("app-control", "close", {"app": "file explorer"})
    assert not r.ok and "Windows shell" in r.summary


def test_kill_is_red(s: SkillEnv) -> None:
    with pytest.raises(ApprovalPending):
        s.call("app-control", "kill", {"app": "notepad"})


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="real desktop app")
def test_open_and_close_notepad_verified(s: SkillEnv) -> None:
    opened = s.call("app-control", "open", {"app": "notepad"})
    try:
        assert opened.ok and opened.evidence["running_pids"]
        assert any(p.name().lower() == "notepad.exe" for p in psutil.process_iter())
    finally:
        closed = s.call("app-control", "close", {"app": "notepad"})
    assert closed.ok and closed.evidence["still_running"] == []
    time.sleep(0.5)
    assert not any(p.name().lower() == "notepad.exe" for p in psutil.process_iter())
