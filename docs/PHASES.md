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
