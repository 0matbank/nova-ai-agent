"""Phase 11 live drill: complex browser flows on the real Browser Worker
(Playwright MCP + headless Chromium), real providers (planner via the router,
vision: local qwen3-vl first), through the real task engine and permissions.

    uv run python scripts/browser_agent_drill.py [rounds]

PASS = every flow COMPLETED with the expected fact in the answer, no approval
needed for these harmless flows, no browser session left open.
"""

import asyncio
import re
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import load_config, load_secrets  # noqa: E402
from core.orchestrator.browser_agent import BrowserAgent  # noqa: E402
from core.orchestrator.executor import UserRequestExecutor  # noqa: E402
from core.queue.engine import TaskContext  # noqa: E402
from core.router.intent import IntentRouter  # noqa: E402
from models.router import ProviderRouter  # noqa: E402
from providers.registry import ProviderRegistry  # noqa: E402
from tests.mocks.browser import real_browser_client  # noqa: E402
from tests.mocks.skills import make_skill_env  # noqa: E402

SHOP = "data:text/html," + urllib.parse.quote("""<title>Nova Test Shop</title>
<h1>Nova Test Shop</h1>
<nav><button onclick="show('home')">Home</button><button onclick="show('prices')">Prices</button>
<button onclick="show('contact')">Contact</button></nav>
<div id=home><p>Welcome. Choose a section above.</p></div>
<div id=prices hidden><h2>Prices</h2><table><tr><th>Item</th><th>Price</th></tr>
<tr><td>Green tea</td><td>245 taka</td></tr>
<tr><td>Mango juice</td><td>180 taka</td></tr></table></div>
<div id=contact hidden><p>Call 01700-000000</p></div>
<script>function show(id){for(const d of ['home','prices','contact'])
document.getElementById(d).hidden = d!==id}</script>""")
CANVAS = "data:text/html," + urllib.parse.quote("""<title>Player</title><body style="margin:0">
<h1>Player</h1><p id=st>Status: stopped</p>
<canvas id=c width=1280 height=500 style="position:absolute;left:0;top:120px"></canvas>
<script>const g=c.getContext('2d');g.fillStyle='#2a2';g.beginPath();g.arc(1000,150,50,0,7);
g.fill();g.fillStyle='#fff';g.beginPath();g.moveTo(985,125);g.lineTo(985,175);g.lineTo(1028,150);
g.fill();c.onclick=e=>{const dx=e.offsetX-1000,dy=e.offsetY-150;
if(dx*dx+dy*dy<2500)document.getElementById('st').innerText=
'Status: playing, track 7 of 12';};</script>""")

DIRECT = [   # (start url, goal, expected text in the answer)
    (SHOP, "Nova Test Shop-এর Prices section-এ গিয়ে Green tea-র দাম কত বলো", r"\b245\b"),
    (CANVAS, "Press the green play button on the player, then tell me the status line",
     r"\b7\b.*\b12\b"),
]
ROUTED = [   # Wikipedia numbers change over time: any figure in the answer counts
    ("en.wikipedia.org-এ Bangladesh খুঁজে তারপর Economy section থেকে GDP কত বলো", r"\d"),
]


def _answer(text: str) -> str:
    """Only the 💬 answer line counts (the page address may contain anything)."""
    line = next((ln for ln in text.splitlines() if ln.startswith("💬")), "")
    return line.translate(str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789"))


async def main() -> int:
    if "-v" in sys.argv:
        import logging
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter("    · %(message)s"))
        for name in ("agent.core", "agent.audit"):
            logging.getLogger(name).addHandler(h)
            logging.getLogger(name).setLevel(logging.INFO)
        sys.argv.remove("-v")
    only = ""
    if "--only" in sys.argv:
        i = sys.argv.index("--only")
        only = sys.argv[i + 1].lower()
        del sys.argv[i:i + 2]
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    tmp = Path(tempfile.mkdtemp(prefix="nova-agent-drill-"))
    client, handle = real_browser_client(tmp)
    s = make_skill_env(tmp, {"browser": client})
    cfg = load_config()
    router = ProviderRouter(cfg.providers, ProviderRegistry.from_config(
        cfg, load_secrets(cfg.path("secrets_dir"))).adapters)
    s.tasks.engine.register("user_request",
                            UserRequestExecutor(IntentRouter(router), router, s.runner))
    fails = 0
    for rnd in range(1, rounds + 1):
        for url, goal, expect in DIRECT:
            if only and only not in goal.lower():
                continue
            t0 = time.time()
            tid = s.new_task()
            task = s.tasks.store.get(tid)
            ctx = TaskContext(task, s.tasks.store, s.tasks.engine.notifier,
                              s.tasks.permissions, s.tasks.approvals)
            try:
                out = await BrowserAgent(s.runner, router).run(ctx, url, "bn" if any(
                    "ঀ" <= ch <= "৿" for ch in goal) else "en")
                ok = bool(out.get("ok")) and bool(re.search(expect, _answer(out.get("answer",
                                                                                   ""))))
                text = out.get("answer", "")
            except Exception as e:  # noqa: BLE001 - a drill reports every failure
                ok, text = False, f"{type(e).__name__}: {e}"
            fails += not ok
            print(f"[{rnd}] {'PASS' if ok else 'FAIL'} {time.time() - t0:5.1f}s {goal[:60]!r}")
            print("      " + text[:900].replace("\n", "\n      "))
        for goal, expect in ROUTED:
            if only and only not in goal.lower():
                continue
            t0 = time.time()
            tid = int(s.tasks.store.create(title=goal, request_text=goal,
                                           task_type="user_request", channel="telegram",
                                           chat_id="555").id)
            for _ in range(4):
                if not await s.tasks.engine.run_once():
                    break
            t = s.tasks.store.get(tid)
            text = t.result_summary or f"{t.error_code}: {t.error_message}"
            ok = t.state.value == "COMPLETED" and bool(re.search(expect, _answer(text)))
            fails += not ok
            print(f"[{rnd}] {'PASS' if ok else 'FAIL'} {time.time() - t0:5.1f}s "
                  f"{t.state.value} {goal[:60]!r}")
            print("      " + text[:900].replace("\n", "\n      "))
    await handle.close_all()
    left = handle.sessions
    print(f"sessions left: {len(left)}; approvals asked: {len(s.tasks.buttons)}; fails: {fails}")
    ok = not (fails or left or s.tasks.buttons)
    print(f"PHASE 11 AGENT DRILL: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
