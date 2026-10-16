"""Nova supervisor (Phase 23): runs core + workers, restarts them, applies /update with
automatic rollback. docs/UPDATE.md

    uv run python -I -S workers/supervisor/run.py          # run (foreground)
    uv run python -I -S workers/supervisor/run.py --stop   # stop a running one gracefully

-S keeps the venv's packages out of this process: during an update `uv sync` may
replace them while the supervisor keeps running (standard library only).
"""

import argparse
import os
import sys
import time
from pathlib import Path

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP))

from core.lifecycle import pid_alive, watch_stop_file  # noqa: E402
from core.supervisor.supervisor import (  # noqa: E402
    Supervisor,
    install_stop_handlers,
    load_settings,
    venv_python,
)

RUN = APP.parent / "data" / "run"
LOCK = RUN / "supervisor.pid"
STOP = RUN / "supervisor.stop"


def running_pid() -> int | None:
    try:
        pid = int(LOCK.read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if pid != os.getpid() and pid_alive(pid) else None


def stop_running(timeout: float = 120) -> int:
    pid = running_pid()
    if pid is None:
        print("no supervisor is running")
        return 0
    STOP.write_text("stop", encoding="utf-8")
    deadline = time.time() + timeout
    while pid_alive(pid) and time.time() < deadline:
        time.sleep(0.5)
    if pid_alive(pid):
        print(f"supervisor {pid} did not stop within {int(timeout)} s", file=sys.stderr)
        return 1
    print(f"supervisor {pid} stopped (core and workers stopped gracefully)")
    return 0


def main() -> int:
    # -I ignores PYTHONIOENCODING: never let a console code page kill the supervisor
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true", help="log only to the log file")
    ap.add_argument("--stop", action="store_true", help="stop the running supervisor")
    args = ap.parse_args()
    RUN.mkdir(parents=True, exist_ok=True)
    if args.stop:
        return stop_running()
    old = running_pid()
    if old is not None:
        print(f"another supervisor is running (pid {old})", file=sys.stderr)
        return 1
    LOCK.write_text(str(os.getpid()))
    python = venv_python(APP)
    sup = Supervisor(APP, load_settings(APP, python), python,
                     log=None if args.quiet else print)
    install_stop_handlers(sup)
    watch_stop_file(sup.stopping.set, str(STOP))
    try:
        return sup.run()
    finally:
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
