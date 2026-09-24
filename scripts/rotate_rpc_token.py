"""Rotate the internal worker RPC token (plan §17D "Token rotate support").

Running workers and the core pick up the new token automatically (they
re-read the file when it changes). The token itself is never printed.

    uv run python scripts/rotate_rpc_token.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.bootstrap import bootstrap  # noqa: E402
from core.ipc.token import TokenStore  # noqa: E402
from core.log import get_logger, shutdown_logging  # noqa: E402


def main() -> int:
    ctx = bootstrap(worker="core", console_logs=False)
    store = TokenStore(ctx.config.path("secrets_dir"))
    store.ensure()
    store.rotate()
    get_logger("audit").info("internal RPC token rotated",
                             extra={"action": "rpc.token.rotate", "status": "ok"})
    shutdown_logging()
    print(f"rotated: {store.path} (value not shown)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
