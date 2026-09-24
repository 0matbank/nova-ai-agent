"""Shared entrypoint for loopback HTTP workers (desktop, browser)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI

from core.bootstrap import EXIT_INVALID_CONFIG, bootstrap
from core.config import ConfigError
from core.ipc.server import make_worker_app, run_worker
from core.ipc.token import TokenStore
from core.log import get_logger, shutdown_logging


def serve(worker_key: str, worker: str, log_category: str,
          register: Callable[[FastAPI], None], argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Nova AI {worker} worker")
    parser.add_argument("--port", type=int, help="override workers.yaml port (tests)")
    parser.add_argument("--config-dir", type=Path)
    parser.add_argument("--root", type=Path)
    args = parser.parse_args(argv)
    try:
        ctx = bootstrap(worker=worker, config_dir=args.config_dir, root=args.root)
    except ConfigError as e:
        print(f"STARTUP REFUSED - {e}", file=sys.stderr)
        return EXIT_INVALID_CONFIG
    cfg = ctx.config.workers
    wcfg = getattr(cfg.workers, worker_key)
    tokens = TokenStore(ctx.config.path("secrets_dir"))
    tokens.ensure()
    app = make_worker_app(worker, cfg.protocol_version, tokens, log_category)
    register(app)
    port = args.port or wcfg.port
    get_logger(log_category).info(f"{worker} worker listening on {wcfg.host}:{port}",
                                  extra={"action": "worker.start", "status": "ok"})
    try:
        run_worker(app, wcfg.host, port)
    finally:
        get_logger(log_category).info(f"{worker} worker stopped",
                                      extra={"action": "worker.stop", "status": "ok"})
        shutdown_logging()
    return 0
