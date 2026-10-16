"""The Nova supervisor (Phase 23, plan §32 / §32A).

- starts core + the desktop and browser workers (venv Python), restarts a crashed one
  with backoff, CRITICAL Telegram alert when one keeps crashing
- core exit 75 → restart everything; 76 → apply the approved update (updater.py)
- stop (Ctrl+C, console close, Windows logoff/shutdown): core first (it flushes its
  state and says "going offline"), then the workers — each by its stop file, killed
  only after the timeout
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.lifecycle import EXIT_RESTART, EXIT_UPDATE, read_json, run_dir
from core.supervisor.process import Child
from core.supervisor.updater import Hooks, Plan, apply_update, read_request

EXIT_INVALID_CONFIG, EXIT_TELEGRAM_AUTH = 2, 3
BACKOFF = (2, 5, 15, 60, 300)
CRASH_WINDOW = 600
DEFAULTS: dict[str, Any] = {"graceful_timeout": 30, "worker_kill_after": 15, "desktop": True,
                            "browser": True, "health_timeout": 180, "stable_seconds": 20,
                            "run_unit_tests": True}


def venv_python(app_dir: Path) -> Path:
    win = app_dir / ".venv" / "Scripts" / "python.exe"
    return win if win.exists() else app_dir / ".venv" / "bin" / "python"


def find_uv() -> str:
    found = shutil.which("uv")
    if found:
        return found
    home = Path.home() / ".local" / "bin" / ("uv.exe" if sys.platform == "win32" else "uv")
    return str(home)


def load_settings(app_dir: Path, python: Path) -> dict[str, Any]:
    """Ask the venv (validated config) — cached so a broken venv can still be restored."""
    cache = app_dir.parent / "data" / "run" / "supervisor-config.json"
    try:
        r = subprocess.run([str(python), "-m", "core.supervisor.describe"], cwd=app_dir,  # noqa: S603
                           capture_output=True, timeout=120, check=False,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        data = json.loads(r.stdout.decode("utf-8")) if r.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        data = None
    if isinstance(data, dict):
        cache = Path(data["data_dir"]) / "run" / "supervisor-config.json"
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data), encoding="utf-8")
        return {**DEFAULTS, **data}
    cached = read_json(cache)
    if cached is None:
        raise RuntimeError("cannot read Nova's config (venv broken?) and no cached copy")
    return {**DEFAULTS, **cached}


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    except OSError:
        pass
    return out


class Supervisor:
    def __init__(self, app_dir: Path, settings: dict[str, Any], python: Path | None = None,
                 log: Callable[[str], None] | None = None) -> None:
        self.app_dir = app_dir
        self.s = settings
        self.python = python or venv_python(app_dir)
        self.run_dir = run_dir(Path(settings["data_dir"]))
        self.log_dir = Path(settings["logs_dir"]) / "supervisor"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._log_file = self.log_dir / "supervisor.log"
        self._external_log = log
        self.stopping = threading.Event()
        self.children: list[Child] = []
        py = str(self.python)
        specs = [("core", "workers/core-service/run.py", True)]
        if settings.get("desktop"):
            specs.append(("desktop", "workers/desktop-worker/run.py", False))
        if settings.get("browser"):
            specs.append(("browser", "workers/browser-worker/run.py", False))
        for name, script, health in specs:
            self.children.append(Child(name, [py, script], app_dir, self.run_dir, self.log_dir,
                                       health=health))
        self.alerted: set[str] = set()
        self.ctrl_handler: object = None

    # ------------------------------------------------------------ helpers

    def log(self, text: str) -> None:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}"
        with self._log_file.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        if self._external_log is not None:
            self._external_log(line)

    def child(self, name: str) -> Child:
        return next(c for c in self.children if c.name == name)

    def alert(self, text: str) -> None:
        """Telegram directly (core may be the thing that is down)."""
        self.log(f"ALERT: {text}")
        env = read_env(Path(self.s["secrets_dir"]) / ".env")
        token = env.get("TELEGRAM_BOT_TOKEN", "")
        chats = [c.strip() for c in env.get("TELEGRAM_ALLOWED_CHAT_IDS", "").split(",")
                 if c.strip()]
        for chat in chats[:3]:
            data = urllib.parse.urlencode({"chat_id": chat, "text": f"🚨 Nova supervisor: {text}"})
            try:
                urllib.request.urlopen(  # noqa: S310 - fixed https Telegram endpoint
                    f"https://api.telegram.org/bot{token}/sendMessage", data.encode(), timeout=20)
            except OSError as e:
                self.log(f"alert not sent: {type(e).__name__}")

    def start_all(self) -> None:
        for c in self.children:
            if not c.running:
                c.start()
                self.log(f"started {c.name} (pid {c.proc.pid if c.proc else '?'})")

    def stop_all(self) -> None:
        core = self.child("core")
        if core.running:
            self.log("stopping core (graceful)")
            code = core.stop(float(self.s["graceful_timeout"]) + 15)
            self.log(f"core stopped (exit {code})")
        for c in self.children:
            if c.name != "core" and c.running:
                code = c.stop(float(self.s["worker_kill_after"]))
                self.log(f"{c.name} stopped (exit {code})")

    def healthy(self, commit: str, timeout: float, stable: float) -> tuple[bool, str]:
        core = self.child("core")
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not core.running:
                return False, f"core exited with code {core.poll()} while starting"
            h = read_json(core.health_file)
            if h is not None and h.get("commit") == commit:
                end = time.time() + stable
                while time.time() < end:
                    if not core.running:
                        return False, f"core exited with code {core.poll()} after start"
                    time.sleep(0.5)
                return True, "up"
            time.sleep(0.5)
        return False, f"no health signal within {int(timeout)} s"

    # ------------------------------------------------------------ events

    def on_core_exit(self, code: int | None) -> None:
        core = self.child("core")
        if code == EXIT_RESTART:
            self.log("core asked for a restart")
            self.stop_all()
            self.start_all()
            return
        if code == EXIT_UPDATE:
            self.update()
            return
        self.crashed(core, code)

    def crashed(self, c: Child, code: int | None) -> None:
        now = time.time()
        c.crashes = [t for t in c.crashes if now - t < CRASH_WINDOW] + [now]
        n = len(c.crashes)
        delay = BACKOFF[min(n - 1, len(BACKOFF) - 1)]
        if c.name == "core" and code in (EXIT_INVALID_CONFIG, EXIT_TELEGRAM_AUTH):
            delay = 300
        c.next_start = now + delay
        c.proc = None
        self.log(f"{c.name} exited (code {code}) — restart in {delay} s ({n}x in 10 min)")
        if n >= 3 and c.name not in self.alerted:
            self.alerted.add(c.name)
            self.alert(f"{c.name} বারবার বন্ধ হয়ে যাচ্ছে ({n} বার, শেষ exit code {code}) — "
                       f"log: {self.log_dir}")

    def update(self) -> None:
        req = read_request(self.run_dir)
        self.stop_all()
        if not req:
            self.log("update requested without a request file — restarting as is")
            self.start_all()
            return
        plan = Plan(app_dir=self.app_dir, python=self.python, uv=find_uv(),
                    database=Path(self.s["database"]), backups_dir=Path(self.s["backups_dir"]),
                    run_dir=self.run_dir, task_id=int(req["task_id"]),
                    from_commit=str(req["from"]), to_commit=str(req["to"]),
                    remote=str(req.get("remote", "origin")), branch=str(req.get("branch", "main")),
                    health_timeout=float(self.s["health_timeout"]),
                    stable_seconds=float(self.s["stable_seconds"]),
                    run_unit_tests=bool(self.s["run_unit_tests"]))
        out = apply_update(plan, Hooks(stop_all=self.stop_all, start_all=self.start_all,
                                       healthy=self.healthy, log=self.log))
        if out.status == "rolled_back" and not out.rollback_ok:
            self.alert(f"update ব্যর্থ এবং rollback-এও সমস্যা: {out.reason[-400:]}")

    # ------------------------------------------------------------ main loop

    def run(self) -> int:
        self.log(f"supervisor started (pid {os.getpid()}, python {self.python})")
        self.start_all()
        while not self.stopping.is_set():
            for c in list(self.children):
                if c.proc is None:
                    if time.time() >= c.next_start and not self.stopping.is_set():
                        c.start()
                        self.log(f"restarted {c.name} (pid {c.proc.pid if c.proc else '?'})")
                    continue
                code = c.poll()
                if code is None:
                    continue
                if c.name == "core":
                    self.on_core_exit(code)
                else:
                    self.crashed(c, code)
            self.stopping.wait(1.0)
        self.stop_all()
        self.log("supervisor stopped")
        return 0


def install_stop_handlers(sup: Supervisor) -> None:
    import signal
    signal.signal(signal.SIGINT, lambda *_: sup.stopping.set())
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, lambda *_: sup.stopping.set())
    if sys.platform == "win32":
        # console close / logoff / Windows shutdown: same graceful procedure (§32A)
        import ctypes
        from ctypes import wintypes

        def handler(event: int) -> bool:
            if event in (2, 5, 6):         # CTRL_CLOSE, CTRL_LOGOFF, CTRL_SHUTDOWN
                sup.stopping.set()
                deadline = time.time() + 25
                while any(c.running for c in sup.children) and time.time() < deadline:
                    time.sleep(0.2)
                return True
            return False
        handler_type = getattr(ctypes, "WINFUNCTYPE")(wintypes.BOOL, wintypes.DWORD)  # noqa: B009
        callback = handler_type(handler)
        sup.ctrl_handler = callback                          # keep it alive
        getattr(ctypes, "windll").kernel32.SetConsoleCtrlHandler(callback, True)  # noqa: B009
