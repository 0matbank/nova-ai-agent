"""Open Nova's local dashboard in the browser (Phase 24, docs/DASHBOARD.md).

    uv run python scripts/dashboard.py            # open it
    uv run python scripts/dashboard.py --rotate   # new token (old links stop working)

The token travels in the URL fragment (#token=…), which browsers never send to a
server; the page keeps it for the tab only. Core must be running (supervisor).
"""

import argparse
import secrets
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import load_config  # noqa: E402
from core.dashboard.api import TOKEN_FILE, dashboard_token  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rotate", action="store_true", help="make a new token")
    ap.add_argument("--print", action="store_true", help="print the link, don't open it")
    args = ap.parse_args()
    cfg = load_config()
    secrets_dir = cfg.path("secrets_dir")
    if args.rotate:
        (secrets_dir / TOKEN_FILE).write_text(secrets.token_urlsafe(32), encoding="utf-8")
        print("new dashboard token — restart Nova (supervisor --stop, then start) to use it")
    token = dashboard_token(secrets_dir)
    url = f"http://127.0.0.1:{cfg.default.dashboard.port}/#token={token}"
    if args.print:
        print(url)
    else:
        webbrowser.open(url)
        print(f"opened http://127.0.0.1:{cfg.default.dashboard.port}/ in the browser")
    return 0


if __name__ == "__main__":
    sys.exit(main())
