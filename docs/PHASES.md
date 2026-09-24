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
