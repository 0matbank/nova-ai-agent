from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.mocks.tasks import TaskEnv, make_task_env

APP_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """A writable copy of the real config/ directory."""
    dst = tmp_path / "config"
    shutil.copytree(APP_DIR / "config", dst)
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
