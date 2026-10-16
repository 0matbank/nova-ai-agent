"""Child processes of the supervisor (core, desktop worker, browser worker)."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from core.lifecycle import ENV_HEALTH_FILE, ENV_STOP_FILE, ENV_SUPERVISED

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def kill_tree(pid: int) -> None:
    """The venv's python.exe is a launcher with the real interpreter as its child."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],  # noqa: S603,S607
                       capture_output=True, creationflags=NO_WINDOW, check=False)
    else:
        import contextlib
        import signal
        with contextlib.suppress(OSError):
            os.killpg(os.getpgid(pid), signal.SIGKILL)


@dataclass
class Child:
    name: str
    argv: list[str]                              # interpreter + script (+ args)
    cwd: Path
    run_dir: Path
    log_dir: Path
    health: bool = False                         # core writes the health file
    proc: subprocess.Popen[bytes] | None = None
    started_at: float = 0.0
    crashes: list[float] = field(default_factory=list)
    next_start: float = 0.0                      # backoff after a crash

    @property
    def stop_file(self) -> Path:
        return self.run_dir / f"{self.name}.stop"

    @property
    def health_file(self) -> Path:
        return self.run_dir / f"{self.name}.health.json"

    def start(self, extra_env: dict[str, str] | None = None) -> None:
        self.stop_file.unlink(missing_ok=True)
        if self.health:
            self.health_file.unlink(missing_ok=True)
        env = {**os.environ, ENV_SUPERVISED: "1", ENV_STOP_FILE: str(self.stop_file),
               "PYTHONIOENCODING": "utf-8", **(extra_env or {})}
        if self.health:
            env[ENV_HEALTH_FILE] = str(self.health_file)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        out = (self.log_dir / f"{self.name}.out.log").open("ab")
        out.write(f"\n===== start {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n".encode())
        kwargs: dict[str, object] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        self.proc = subprocess.Popen(self.argv, cwd=self.cwd, env=env, stdout=out,  # noqa: S603
                                     stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     **kwargs)  # type: ignore[call-overload]
        out.close()
        self.started_at = time.time()

    def poll(self) -> int | None:
        return None if self.proc is None else self.proc.poll()

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self, timeout: float) -> int | None:
        """Graceful: the stop file, then wait; kill the tree after the timeout."""
        if not self.running:
            return self.poll()
        assert self.proc is not None
        self.stop_file.write_text("stop", encoding="utf-8")
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_tree(self.proc.pid)
            try:
                return self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                return None
        finally:
            self.stop_file.unlink(missing_ok=True)
