from __future__ import annotations

import shutil
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.mocks.tasks import TaskEnv, make_task_env

APP_DIR = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(autouse=True)
def _never_touch_the_real_broker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Phase 22/24 live finding: a full-service test reached the owner's REAL elevated
    broker (refused: wrong token). No test may talk to it or start its scheduled task."""
    import os
    import tempfile

    from core.broker import client
    real_init = client.BrokerClient.__init__

    def init(self: client.BrokerClient, cfg: Any, tokens: Any) -> None:
        real_init(self, cfg, tokens)
        self.pipe = rf"\\.\pipe\nova-test-no-broker-{os.getpid()}"
        self.install_dir = Path(tempfile.gettempdir()) / "nova-test-no-broker"
    monkeypatch.setattr(client.BrokerClient, "__init__", init)
    monkeypatch.setattr(client.BrokerClient, "start_task", lambda self: False)


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """A writable copy of the real config/ directory. Worker ports are moved to
    free ports so tests never talk to real workers running on this PC."""
    dst = tmp_path / "config"
    shutil.copytree(APP_DIR / "config", dst)
    workers = dst / "workers.yaml"
    data = yaml.safe_load(workers.read_text(encoding="utf-8"))
    for key in ("desktop_worker", "browser_worker"):
        data["workers"][key]["port"] = _free_port()
    workers.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    # the real Nova's dashboard holds 47810 while tests run
    default = dst / "default.yaml"
    d = yaml.safe_load(default.read_text(encoding="utf-8"))
    d.setdefault("dashboard", {})["port"] = _free_port()
    default.write_text(yaml.safe_dump(d, allow_unicode=True), encoding="utf-8")
    return dst


@pytest.fixture
def runtime_root(tmp_path: Path) -> Path:
    root = tmp_path / "runtime"
    root.mkdir()
    return root


@pytest.fixture
def task_env(tmp_path: Path) -> TaskEnv:
    return make_task_env(tmp_path / "db" / "agent.db", tmp_path / "backups")


@pytest.fixture
def edit_yaml(config_dir: Path) -> Callable[[str, Callable[[dict[str, Any]], None]], None]:
    """edit_yaml('workers', lambda d: d[...].update(...)) mutates a config copy."""

    def _edit(name: str, fn: Callable[[dict[str, Any]], None]) -> None:
        path = config_dir / f"{name}.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        fn(data)
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    return _edit


@pytest.fixture(autouse=True)
def _not_under_the_real_supervisor(monkeypatch: pytest.MonkeyPatch) -> None:
    """V1.1 finding: a shell started from Nova's own process tree carries the
    supervisor's markers, so a test service thought it was supervised and sent the
    "back online" notice. Tests that need them set them themselves."""
    from core import lifecycle as lc
    for name in (lc.ENV_SUPERVISED, lc.ENV_SUPERVISOR_PID, lc.ENV_STOP_FILE,
                 lc.ENV_HEALTH_FILE):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _never_the_real_recycle_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every delete goes to the Recycle Bin now: tests must never touch the owner's."""
    import sys

    import send2trash as s2t

    bin_dir = tmp_path / "fake-recycle-bin"

    def fake(p: Any) -> None:
        bin_dir.mkdir(exist_ok=True)
        dest, n = bin_dir / Path(p).name, 1
        while dest.exists():                    # the real bin keeps same-named items too
            dest, n = bin_dir / f"{Path(p).name}.{n}", n + 1
        Path(p).rename(dest)

    monkeypatch.setattr(s2t, "send2trash", fake)
    mod = sys.modules.get("nova_skill_files_delete")
    if mod is not None:
        monkeypatch.setattr(mod, "send2trash", fake)
