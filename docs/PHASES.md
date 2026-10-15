# Build Progress (plan §41)

| Phase | Name | Status | Verification |
|---|---|---|---|
| 0 | Repository Foundation | ✅ PASS | `python scripts/verify_foundation.py` |
| 1 | Config + Dependency + Logging Foundation | ✅ PASS | `uv sync --frozen`, `uv run python -m core.bootstrap --check`, `uv run pytest` |
| 2 | Core Service + Telegram + Authentication | ✅ PASS (real phone /status verified 2026-09-24, CI green) | `uv run pytest tests/integration/test_telegram_core.py`; real: `uv run python workers/core-service/run.py` then `/status` from phone |
| 3 | Task DB + Queue + State Machine | ✅ PASS (phone: create → /tasks → /cancel → restart → history kept; 112 tests) | `uv run pytest tests/integration/test_task_engine.py tests/integration/test_db_migrations.py` |
| 4 | Permission + Approval + Audit | ✅ PASS (phone drill: Reject → not executed; Approve → executed once, approval USED; 136 tests) | `uv run pytest tests/security/test_permissions.py`; real: stop service, `uv run python scripts/approval_drill.py` |
| 5 | IPC + Worker Skeleton | ✅ PASS (live: 14/14 checks on real workers + pipe; 167 tests) | start the 3 workers, then `uv run python scripts/verify_ipc.py` |
| 6 | Windows + File + PowerShell Skills | ✅ PASS (live drill 22/22 on real PC; 264 tests) | start desktop worker, then `uv run python scripts/pc_control_drill.py`; phone: `/skills` |
| 7 | Screenshot + Windows UI Automation | ✅ PASS (live UI drill 12/12; phone /screenshot + /status; 300 tests) | desktop worker + `uv run python scripts/ui_drill.py`; phone: `/screenshot` |
| 8 | Provider Abstraction + Ollama | ✅ PASS (phone: local-AI answers via router, honest refusal for news; §9A benchmark 10/10 with thinking; 355 tests) | `uv run python scripts/benchmark_local_models.py`; phone: any question |
| 9 | Voice + faster-whisper | ✅ PASS (phone: Bangla/English/Banglish voice; large-v3 on GPU ~0.5–1.7 s; unclear → suggestion + ✅, never guesses; 385 tests) | phone: send a voice message; `uv run pytest -m e2e tests/integration/test_voice.py` |
| 10 | Browser Worker + Playwright CLI | ✅ PASS (phone: site open / in-site search / download; live drill 25/25 + 6/6 with real AI; Bangla summary via Gemini with automatic local fallback seen live on quota; 468 tests) | start browser worker, then `uv run python scripts/browser_drill.py`; phone: `en.wikipedia.org-এ Dhaka সার্চ করো` |
| 11 | Playwright MCP + Vision Fallback | ✅ PASS (phone: Wikipedia search → article → GDP, grounded, 3 steps; live drill: shop tab flow + canvas-only button via local vision + real Wikipedia, 11/12 then 3/3; vision click accuracy within ~10 px; 493 tests) | browser worker running, then `uv run python scripts/browser_agent_drill.py 3`; phone: `en.wikipedia.org-এ Bangladesh খুঁজে তারপর Economy section থেকে GDP কত বলো` |
| 12 | OpenAI Codex Adapter + Coding Flow | ✅ PASS (phone: demo project bug fix → Codex edit → Nova's own test run 4/4 ✅, approval path seen live on a dirty tree; live drill PASS, nothing committed; 514 tests) | `uv run python scripts/coding_drill.py`; phone: `codex demo project-er test fail korche, bug fix koro` |
| 13 | Google Antigravity Adapter | ✅ PASS (phone: general question answered via Antigravity ~18 s; live drill: same coding task via Antigravity 49 s, 4/4 tests, nothing committed + Bangla reasoning; summary fallback Gemini→Antigravity seen live; browser agent drill 3/3; 533 tests) | `uv run python scripts/antigravity_setup.py`, then `uv run python scripts/coding_drill.py --only google_antigravity`; phone: any general question |
| 14 | Multi-Provider Failover Engine | ✅ PASS (live drill: real Codex fixed part, simulated 503 → circuit open → real Antigravity finished from the diff, 4/4 tests, nothing committed; phone: /status + question via Antigravity (task 29), then fast-answer tiers on phone: local 3.8 s, Gemini 4.4 s, live info 30 s; 573 tests) | `uv run python scripts/failover_drill.py`; phone: `/status`, any question |


### Notes — Phase 10
- Owner rule: replies follow the owner's language (Bangla/Banglish → Bangla, English → English); web originals stay, with a summary in the owner's language.
- Gemini API text adapter pulled forward from Phase 14 at the owner's request (2026-09-25), routed only for `summarization` (`free_tier_only`); reasoning/vision routing stays for Phase 14.
- Bot checks (CAPTCHA) are never solved → task ends `BLOCKED_NEEDS_USER`.
- General web search without a named site = Phase 16 research agent.

### Notes — Phase 11
- Engines: Playwright CLI (routine) + Playwright MCP 0.0.82 (multi-step), same session API and permissions.
- Planner = router `reasoning` (local qwen3:8b now; Codex/Antigravity automatically once built). Vision = local qwen3-vl:8b first, Gemini fallback (owner choice).
- Known limit: the small local planner sometimes wanders on real sites (1 miss in 12 live runs); the step cap / repeat guard stop it honestly with the steps shown.

### Notes — Phase 12
- Codex CLI 0.157.0 (pinned) signed in with ChatGPT (owner's plan); an API-key login is treated as AUTH_REQUIRED. No password is stored — only the CLI's own credential store.
- Sandbox: read-only unless `code.edit` is granted for a registered project folder, then workspace-write, no network, never commit/push. Nova's own folders are never editable.
- `code.edit` YELLOW: automatic on a clean git tree, otherwise the owner approves first (owner choice). Nova's test command decides success; one follow-up round, then BLOCKED_NEEDS_USER.
- Windows: Codex's sandbox cannot write under %TEMP% — project folders live under the workspace.

### Notes — Phase 13
- Official Antigravity CLI `agy` 1.2.11 (Google, auto-updates itself — cannot be pinned) signed in with the owner's Google account (AI Pro quota); credentials stay in the system keyring. Health = `agy models` (no quota).
- Safety rules live in agy's own settings (`scripts/antigravity_setup.py`, backup first): no web fetch/actuation, no history-changing git, nothing outside the workspace, never paid Google One credits. Health refuses to run the agent if they drift. agy's built-in Google search tool is not covered by permissions (queries go only to Google).
- Windows: agy's terminal sandbox is a preview needing admin, so print mode refuses every terminal command; the agent edits with file tools and Nova runs the tests. Coding flow now sends the project's file list to every provider.
- Routing: coding Codex → Antigravity → local; reasoning Antigravity first; summaries Gemini API → Antigravity → local; browser planner = new `planning` task type, local first with Antigravity backup (owner choice).

### Notes — Phase 14
- Circuit breaker: 3 transient failures in a row → 10/20/30 min cooldown; usage limit → the provider's retry-after; probe before reuse; state in `provider_state` (survives restart). Every call recorded in `provider_calls`; a mostly-failing provider ranks lower.
- Handoff: the next provider gets the failure summary; the coding flow adds the current git diff, and the report says who took over.
- Idempotency ledger (`side_effects`): side-effecting skill actions run at most once per task; an unknown outcome stops for the owner.
- Claude Code adapter built but disabled (subscription only — API-key variables are stripped). Gemini API = reasoning fallback (free tier).
- Owner 2026-10-15 ("1 line answer took a minute"): quick questions → local qwen3 kept in GPU memory (warmed at start); numbers/dates → Gemini first; current info → Antigravity with Google search for that request only; complex → Antigravity; a not-sure local answer escalates silently. News/weather are answered instead of refused.
- Audit of Phases 0–14 (2026-10-15): 22 confirmed gaps; fixed in follow-up commits (see git log).
