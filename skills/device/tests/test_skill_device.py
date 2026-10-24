"""V1.2 device skill: the real worker endpoints and permissions. The real speaker level is
only read and set to what it already is (nothing audible changes); media keys and
brightness writes are stubbed — a test never plays/pauses the owner's music or dims the
owner's screen."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from core.ipc.client import WorkerClient
from core.ipc.server import make_worker_app
from core.ipc.token import TokenStore
from tests.mocks.skills import make_skill_env

APP_DIR = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows device controls")


def _worker(tmp_path: Path) -> tuple[WorkerClient, Any]:
    spec = importlib.util.spec_from_file_location(
        "svc_desktop_for_device", APP_DIR / "workers" / "desktop-worker" / "service.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    tokens = TokenStore(tmp_path / "tok")
    tokens.ensure()
    app = make_worker_app("desktop", 1, tokens, "desktop")
    mod.register(app)
    client = WorkerClient("desktop", "127.0.0.1", 1, tokens, 1, client=httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 5555))))
    return client, mod


def test_volume_is_read_and_set_with_proof(tmp_path: Path) -> None:
    client, _ = _worker(tmp_path)
    s = make_skill_env(tmp_path, {"desktop": client})
    now = s.call("device", "volume", {})
    assert now.ok and 0 <= now.evidence["level"] <= 100           # a real reading
    same = s.call("device", "volume", {"level": now.evidence["level"]})
    assert same.ok and same.evidence["before"]["level"] == same.evidence["after"]["level"]
    assert "→" in same.summary


def test_media_and_brightness_go_through_the_worker(tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    client, mod = _worker(tmp_path)
    sent: list[str] = []
    monkeypatch.setattr(mod.desktop_device, "media",
                        lambda action: sent.append(action) or {"sent": action})
    monkeypatch.setattr(mod.desktop_device, "brightness", lambda level, change: {
        "screens": [{"screen": "test", "via": "DDC/CI", "before": 40, "after": level}]})
    s = make_skill_env(tmp_path, {"desktop": client})
    r = s.call("device", "media", {"action": "next"})
    assert r.ok and sent == ["next"] and "⏭" in r.summary
    r = s.call("device", "brightness", {"level": 60})
    assert r.ok and "40 → 60" in r.summary
    with pytest.raises(Exception):                                 # noqa: B017 - validation
        s.call("device", "media", {"action": "shutdown"})


def test_no_brightness_support_is_said_plainly(tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    client, mod = _worker(tmp_path)

    def unsupported(level: Any, change: Any) -> Any:
        raise mod.desktop_ui.UIError("no_brightness: this screen does not let Windows "
                                     "change its brightness")
    monkeypatch.setattr(mod.desktop_device, "brightness", unsupported)
    s = make_skill_env(tmp_path, {"desktop": client})
    r = s.call("device", "brightness", {"change": 10})
    assert not r.ok and "no_brightness" in r.summary
