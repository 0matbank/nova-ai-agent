"""Print what the (stdlib-only) supervisor needs from the validated config, as JSON.
Run with the venv Python:  python -m core.supervisor.describe"""

from __future__ import annotations

import json
import sys


def describe() -> dict[str, object]:
    from core.config import load_config
    cfg = load_config()
    w = cfg.workers.workers
    u = cfg.default.update
    return {
        "data_dir": str(cfg.path("data_dir")), "logs_dir": str(cfg.path("logs_dir")),
        "secrets_dir": str(cfg.path("secrets_dir")), "backups_dir": str(cfg.path("backups_dir")),
        "database": str(cfg.path("database")),
        "graceful_timeout": cfg.default.shutdown.graceful_timeout_seconds,
        "worker_kill_after": cfg.default.shutdown.worker_kill_after_seconds,
        "desktop": w.desktop_worker.enabled, "browser": w.browser_worker.enabled,
        "health_timeout": u.health_timeout_seconds, "stable_seconds": u.stable_seconds,
        "run_unit_tests": u.run_unit_tests,
    }


if __name__ == "__main__":
    json.dump(describe(), sys.stdout)
