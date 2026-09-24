"""Privileged/Admin Broker — INTERFACE SKELETON (plan §4, §17A; built in Phase 22).

Serves the restricted named pipe with a single harmless action, "ping". No
elevated action exists yet, and the broker never runs arbitrary commands.

    uv run python workers/privileged-broker/run.py
"""

import os
import signal
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.bootstrap import bootstrap  # noqa: E402
from core.ipc.pipe import PipeServer  # noqa: E402
from core.ipc.token import TokenStore  # noqa: E402
from core.log import get_logger, shutdown_logging  # noqa: E402

ALLOWLIST = {"ping": lambda params: {"pong": True, "pid": os.getpid()}}


def main() -> int:
    ctx = bootstrap(worker="broker")
    cfg = ctx.config.workers
    tokens = TokenStore(ctx.config.path("secrets_dir"))
    tokens.ensure()
    server = PipeServer(cfg.workers.privileged_broker.pipe_name, cfg.protocol_version,
                        tokens, ALLOWLIST)
    log = get_logger("core")
    log.info(f"broker skeleton serving {cfg.workers.privileged_broker.pipe_name} "
             f"(allowlist: {sorted(ALLOWLIST)})", extra={"action": "broker.start"})
    done = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: done.set())
    server.start()
    try:
        done.wait()
    finally:
        server.stop()
        shutdown_logging()
    return 0


if __name__ == "__main__":
    sys.exit(main())
