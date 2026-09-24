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
