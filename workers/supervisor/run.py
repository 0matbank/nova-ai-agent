"""Nova supervisor (Phase 23): runs core + workers, restarts them, applies /update with
automatic rollback. docs/UPDATE.md

    uv run python -I -S workers/supervisor/run.py

-S keeps the venv's packages out of this process: during an update `uv sync` may
replace them while the supervisor keeps running (standard library only).
"""

import argparse
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP))

from core.supervisor.supervisor import (  # noqa: E402
    Supervisor,
    install_stop_handlers,
    load_settings,
    venv_python,
)


def main() -> int:
    # -I ignores PYTHONIOENCODING: never let a console code page kill the supervisor
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true", help="log only to the log file")
    args = ap.parse_args()
    lock = APP.parent / "data" / "run" / "supervisor.pid"
    lock.parent.mkdir(parents=True, exist_ok=True)
    if lock.exists():
        try:
            import os
            old = int(lock.read_text().strip())
            if old != os.getpid() and _alive(old):
                print(f"another supervisor is running (pid {old})", file=sys.stderr)
                return 1
        except ValueError:
            pass
    import os
    lock.write_text(str(os.getpid()))
    python = venv_python(APP)
    sup = Supervisor(APP, load_settings(APP, python), python,
                     log=None if args.quiet else print)
    install_stop_handlers(sup)
    try:
        return sup.run()
    finally:
        lock.unlink(missing_ok=True)


def _alive(pid: int) -> bool:
    import subprocess
    r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],  # noqa: S603,S607
                       capture_output=True, text=True, check=False)
    return str(pid) in r.stdout


if __name__ == "__main__":
    sys.exit(main())
