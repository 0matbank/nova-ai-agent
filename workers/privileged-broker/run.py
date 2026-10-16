"""Privileged/Admin Broker — DEVELOPMENT launcher (Phase 22).

Runs the broker package from the repo, NOT elevated, with a policy built from
config/workers.yaml (state in <data>/broker-dev). Admin operations then answer
NOT_ELEVATED; ping/status/validation/approval checks work as in production.

The real broker is the protected, elevated copy installed by
`uv run python scripts/broker_install.py` (docs/BROKER.md). Don't run both: the
first one owns the pipe name.

    uv run python workers/privileged-broker/run.py
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))

from nova_broker.__main__ import main as broker_main  # noqa: E402

from core.bootstrap import bootstrap  # noqa: E402
from core.broker.policy import build_policy  # noqa: E402
from core.ipc.token import TokenStore  # noqa: E402


def main() -> int:
    ctx = bootstrap(worker="broker")
    cfg = ctx.config
    TokenStore(cfg.path("secrets_dir")).ensure()
    dev = cfg.path("data_dir") / "broker-dev"
    dev.mkdir(parents=True, exist_ok=True)
    policy = build_policy(cfg, install_dir=dev, folders={}, winget="")
    policy["log_dir"] = str(cfg.path("logs_dir") / "broker")
    path = dev / "policy.json"
    path.write_text(json.dumps(policy, indent=2), encoding="utf-8")
    return broker_main(["--policy", str(path)])


if __name__ == "__main__":
    sys.exit(main())
