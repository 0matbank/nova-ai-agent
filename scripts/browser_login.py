"""Log in once to a website in Nova's own browser profile (plan §16: a separate
agent browser profile; the login session stays on this PC only).

    uv run python scripts/browser_login.py https://example.com/login

A visible browser window opens with the agent profile (sessions/browser/default).
Log in yourself, then press Enter here: the window closes and the session is kept,
so later tasks on that site are already logged in. Nova never sees or stores the
password — only the browser's own cookies stay in the profile folder.
Needs the browser worker running (uv run python workers/browser-worker/run.py).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.bootstrap import bootstrap  # noqa: E402
from core.ipc.client import WorkerClient  # noqa: E402
from core.ipc.token import TokenStore  # noqa: E402
from core.skills.urls import check_url  # noqa: E402

PROFILE = "default"


async def main(url: str) -> int:
    check_url(url)                                   # same URL policy as every browser task
    ctx = bootstrap(worker="script", console_logs=False)
    w = ctx.config.workers
    client = WorkerClient("browser", w.workers.browser_worker.host,
                          w.workers.browser_worker.port,
                          TokenStore(ctx.config.path("secrets_dir")), w.protocol_version)
    try:
        opened = await client.call("POST", "/v1/sessions", task_id="login", json={
            "url": url, "profile": PROFILE, "headed": True, "engine": "cli"})
        if not opened.get("ok", True) or "session_id" not in opened:
            print(f"could not open the browser: {opened.get('error') or opened}")
            print("Is the browser worker running, and no Nova task using the browser now?")
            return 1
        sid = opened["session_id"]
        print("🌐 Nova-র browser খুলেছে। সেখানে নিজে login করুন।")
        print("   Login শেষ হলে এখানে Enter চাপুন (window বন্ধ হবে, login থেকে যাবে)।")
        await asyncio.get_running_loop().run_in_executor(None, input)
        await client.call("DELETE", f"/v1/sessions/{sid}", task_id="login")
        print("✅ Login রাখা হয়েছে — এখন থেকে Nova এই site-এ logged-in অবস্থায় কাজ করবে।")
        return 0
    finally:
        await client.aclose()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(asyncio.run(main(sys.argv[1])))
