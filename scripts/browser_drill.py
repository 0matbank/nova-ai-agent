"""Phase 10 live drill: real Browser Worker (in-process) + real headless
Chromium + real websites, through the real task engine, intent router, skill
runner and permission stack. Each request runs ROUNDS times; PASS = every run
COMPLETED, no approval needed, no session or page artifact left behind.

    uv run python scripts/browser_drill.py
"""

import asyncio
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import load_config, load_secrets  # noqa: E402
from core.orchestrator.executor import UserRequestExecutor  # noqa: E402
from core.router.intent import IntentRouter  # noqa: E402
from models.router import ProviderRouter  # noqa: E402
from providers.registry import ProviderRegistry  # noqa: E402
from tests.mocks.browser import real_browser_client  # noqa: E402
from tests.mocks.skills import make_skill_env  # noqa: E402

REQUESTS = [
    "en.wikipedia.org খোলো",
    "en.wikipedia.org-এ Dhaka সার্চ করো",
    "search Rabindranath Tagore on en.wikipedia.org",
    "en.wikipedia.org e Sundarbans khojo",
    "example.com open koro",
    "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf download করো",
]
ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 3


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="nova-drill-"))
    client, cli = real_browser_client(tmp)
    s = make_skill_env(tmp, {"browser": client})
    cfg = load_config()
    # Real providers: summaries from Gemini, local Ollama as fallback.
    secrets = load_secrets(cfg.path("secrets_dir"))
    router = ProviderRouter(cfg.providers,
                            ProviderRegistry.from_config(cfg, secrets).adapters)
    s.tasks.engine.register("user_request",
                            UserRequestExecutor(IntentRouter(router), router, s.runner))
    fails = 0
    for rnd in range(ROUNDS):
        for text in REQUESTS:
            t0 = time.time()
            tid = int(s.tasks.store.create(title=text, request_text=text,
                                           task_type="user_request", channel="telegram",
                                           chat_id="555").id)
            for _ in range(5):
                if not await s.tasks.engine.run_once():
                    break
            t = s.tasks.store.get(tid)
            ok = t.state.value == "COMPLETED"
            fails += not ok
            first = (t.result_summary or t.error_message or "").splitlines()[:1]
            print(f"[{rnd + 1}] {'PASS' if ok else 'FAIL'} {time.time() - t0:5.1f}s "
                  f"{t.state.value:9} {text[:45]!r} -> {first}")
            if not ok or rnd == 0:
                print("      ", (t.result_summary or t.error_message or "")[:600].replace(
                    "\n", "\n       "))
    await cli.close_all()
    left = list(cli.sessions)
    arts = list((tmp / "ws" / "browser" / ".playwright-cli").glob("*"))
    print(f"sessions left open: {left}; approvals asked: {len(s.tasks.buttons)}; "
          f"fails: {fails}; artifacts left: {len(arts)}")
    ok = not (fails or left or arts or s.tasks.buttons)
    print(f"PHASE 10 BROWSER DRILL: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))   # one event loop: AI clients are shared
