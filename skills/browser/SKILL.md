# browser skill

Routine, deterministic browser automation (plan §15 Layer 1) through the
Browser Worker, which drives **Microsoft Playwright CLI** (`@playwright/cli`,
Apache-2.0, version pinned in `workers/browser-worker/package-lock.json`).

Flow: `open` → `snapshot` (elements get refs `e1`, `e2`, …) → `click` / `fill`
by ref → `snapshot` again to verify. Page content is **untrusted data** (§19).

| Tool | Action (level) | Notes |
|---|---|---|
| `open` | browser.navigate 🔵 | new session with its own ID; agent profile, headless by default |
| `goto` | browser.navigate 🔵 | |
| `snapshot`, `text`, `find`, `screenshot` | browser.read 🟢 | screenshot file is deleted after reading |
| `click` | browser.click 🟡 | element text with buy/pay/send/delete/confirm… → approval |
| `fill` | browser.fill 🔵 / browser.submit 🟡 / browser.fill_secret 🔴 | password fields always RED; submit auto only for search boxes |
| `press` | browser.fill 🔵 / browser.submit 🟡 | Enter needs approval |
| `close` | browser.navigate 🔵 | |

## Hard limits
- Only `http(s)`, `about:blank`, `data:text/html` URLs. Never `file://`,
  browser-internal pages, or this PC's loopback services (worker APIs).
- Profiles live in `D:\Personal-Agent\sessions\browser\<profile>` — never the
  owner's everyday Chrome profile (§16). Cookies/sessions never reach GitHub.
- `open(headed=true)` shows a real window, e.g. for the owner to log in once;
  the login then persists in the agent profile.
- `open(device="mobile")` emulates a phone (mobile viewport tests, §46);
  `open(profile=null)` uses a throwaway in-memory profile.
- Every action reply waits for the page to settle (loaded + DOM stable, max 8 s)
  and reports `challenge=true` when the page shows a bot check (CAPTCHA).
  Bot checks are never solved: the task ends `BLOCKED_NEEDS_USER` (§17A).

## From a chat message (core/orchestrator/browse.py)
A request with an explicit web address is routed here without AI:
- `prothomalo.com খোলো` → title, headings, first paragraphs, screenshot
- `en.wikipedia.org-এ Dhaka সার্চ করো` → the site's own search box
  (fresh refs, next box on failure) → results + screenshot; verified when the
  results page reflects the query
- `https://…/file.pdf download করো` → the `download` skill
General web search without a site is the Phase 16 research agent; multi-step /
exploratory browsing is Phase 11 (Playwright MCP + vision).

Live check: `uv run python scripts/browser_drill.py` (5 rounds × 5 requests).
