"""Process lifecycle shared by core, the workers and the supervisor (Phase 23, plan §32A).
Standard library only — the supervisor imports this without the venv.

- Exit codes: core asks the supervisor for a restart (75) or an update (76).
- Stop file: the supervisor asks a child to stop gracefully by creating the file
  named in NOVA_STOP_FILE (works without a console, unlike Ctrl-Break).
- Health file: core writes NOVA_HEALTH_FILE once it is fully up (Telegram, DB,
  skills) — the supervisor's proof that a (new) version really started.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_RESTART = 75
EXIT_UPDATE = 76
ENV_SUPERVISED = "NOVA_SUPERVISED"
ENV_STOP_FILE = "NOVA_STOP_FILE"
ENV_HEALTH_FILE = "NOVA_HEALTH_FILE"
UPDATE_REQUEST = "update-request.json"
UPDATE_RESULT = "update-result.json"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def supervised() -> bool:
    return os.environ.get(ENV_SUPERVISED) == "1"


def run_dir(data_dir: Path) -> Path:
    path = data_dir / "run"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def git(app_dir: Path, *args: str, timeout: int = 120) -> tuple[int, str]:
    try:
        r = subprocess.run(["git", "-C", str(app_dir), *args], capture_output=True,  # noqa: S603,S607
                           timeout=timeout, creationflags=NO_WINDOW, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, str(e)
    return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace").strip()


def head_commit(app_dir: Path) -> str:
    rc, out = git(app_dir, "rev-parse", "HEAD")
    return out if rc == 0 else ""


def watch_stop_file(on_stop: Callable[[], None], path: str | None = None,
                    every: float = 0.5) -> threading.Thread | None:
    """Call on_stop once when the stop file appears (it is removed first)."""
    target = path or os.environ.get(ENV_STOP_FILE)
    if not target:
        return None
    stop_file = Path(target)
    stop_file.unlink(missing_ok=True)                 # a stale request from last time

    def loop() -> None:
        while True:
            if stop_file.exists():
                stop_file.unlink(missing_ok=True)
                on_stop()
                return
            time.sleep(every)
    t = threading.Thread(target=loop, daemon=True, name="stop-file")
    t.start()
    return t


def write_health(extra: dict[str, Any], app_dir: Path) -> None:
    target = os.environ.get(ENV_HEALTH_FILE)
    if not target:
        return
    write_json(Path(target), {"pid": os.getpid(), "commit": head_commit(app_dir),
                              "started_at": time.time(), **extra})


class ExitRequest:
    """How core leaves: plain stop (0), restart (75) or update (76) — main() returns it."""

    def __init__(self) -> None:
        self.code = EXIT_OK
        self.reason = "stop"

    def set(self, code: int, reason: str) -> None:
        self.code, self.reason = code, reason
