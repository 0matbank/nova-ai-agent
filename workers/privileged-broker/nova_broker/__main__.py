"""Start the broker.

    pythonw.exe -I -m nova_broker [--policy PATH]

Installed: the protected copy reads <install>\\policy.json (the default: next to its
Python). Development: workers/privileged-broker/run.py builds a policy from the repo
config and runs this unelevated (admin operations then answer NOT_ELEVATED).
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
from pathlib import Path

from nova_broker import VERSION
from nova_broker.policy import Policy
from nova_broker.server import Broker, PipeServer, log, setup_logging


def default_policy() -> Path:
    return Path(sys.prefix).parent / "policy.json"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nova_broker")
    ap.add_argument("--policy", type=Path, default=default_policy())
    args = ap.parse_args(argv)
    policy = Policy.load(args.policy)
    setup_logging(policy.log_dir)
    broker = Broker(policy)
    server = PipeServer(broker)
    try:
        server.start()
    except RuntimeError as e:
        log.critical("%s", e)
        return 2
    log.info("broker %s serving %s (elevated=%s, policy=%s)", VERSION, policy.pipe_name,
             broker.elevated, policy.summary())
    done = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: done.set())
    signal.signal(signal.SIGTERM, lambda *_: done.set())
    try:
        done.wait()
    finally:
        server.stop()
        log.info("broker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
