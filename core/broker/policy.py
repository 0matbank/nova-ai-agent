"""Build the broker's policy.json from config/workers.yaml (Phase 22).

The installer writes it into the protected install folder; the development launcher
(workers/privileged-broker/run.py) writes one into the data folder.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.config.schema import AppConfig
from core.ipc.token import TOKEN_FILE

Resolver = Callable[[str], str]        # "%SystemRoot%" / "%ProgramFiles%" → real folder


def expand(arg: str, folders: dict[str, str]) -> str:
    for key, real in folders.items():
        if arg.lower().startswith(key.lower()):
            return real + arg[len(key):]
    return arg


def build_policy(cfg: AppConfig, *, install_dir: Path, folders: dict[str, str],
                 winget: str = "", require_elevation: bool = True) -> dict[str, Any]:
    b = cfg.workers.workers.privileged_broker
    return {
        "pipe_name": b.pipe_name,
        "protocol_version": cfg.workers.protocol_version,
        "token_file": str(cfg.path("secrets_dir") / TOKEN_FILE),
        "db_path": str(cfg.path("database")),
        "state_dir": str(install_dir / "state"),
        "log_dir": str(install_dir / "logs"),
        "services": list(b.services),
        "tasks": {name: [expand(a, folders) for a in argv] for name, argv in b.tasks.items()},
        "winget": winget if b.packages else "",
        "approval_max_age_seconds": b.approval_max_age_seconds,
        "require_elevation": require_elevation,
    }
