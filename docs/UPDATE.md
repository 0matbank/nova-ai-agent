# Restart, update and rollback (Phase 23)

Plan §32A (graceful shutdown) + Phase 23 ("state is flushed before an update/restart,
and a failed update rolls back by itself").

## Running Nova under the supervisor

```
uv run python -I -S workers/supervisor/run.py
```

The supervisor starts core, the desktop worker and the browser worker, restarts a
crashed one (backoff 2 s → 5 min; a CRITICAL Telegram message after 3 crashes in 10
min), and handles `/restart-agent` and `/update`. It uses only Python's standard
library (`-S`), so during an update `uv sync` can replace the venv's packages while
it keeps running. Logs: `logs/supervisor/` (one `*.out.log` per child).

Stopping it (Ctrl+C, closing its window, Windows logoff/shutdown) stops core first —
core says "going offline" and saves its state — then the workers.

## Graceful shutdown (§32A)

1. Telegram polling stops (new messages wait on Telegram until Nova is back)
2. no new task starts; the running task stops at its next checkpoint → RETRYING
   (`INTERRUPTED_BY_RESTART`, not counted as a failure) and resumes from there
3. scheduler, watchers and AI requests end; "🔌 Nova … offline" to the owner
4. DB closed, logs flushed; workers stop by their stop file (killed only after
   `shutdown.worker_kill_after_seconds`)

## /update

1. Nova checks: running under the supervisor, the code folder has no uncommitted
   changes, `git fetch origin main` has newer commits that fast-forward the current one
2. the owner sees the commit list and approves (`system.self_update`, RED)
3. Nova goes offline gracefully; the supervisor:
   - backs up the database (`backups/pre-update-<time>-<commit>.db`)
   - `git merge --ff-only <approved commit>`
   - `uv sync --frozen --all-extras` (plain `--frozen` would uninstall voice/desktop)
   - preflight with the new code: `python -m core.bootstrap --check` + `tests/unit`
   - starts everything; the new core must report healthy within
     `update.health_timeout_seconds` and keep running `update.stable_seconds`
4. any step fails → **automatic rollback**: stop, `git reset --hard <old commit>`,
   `uv sync` again, restore the DB backup if the schema changed, start the old version
5. the same /update task reports the result after the restart (✅ updated, or ↩️ rolled
   back with the failed step and reason)

Nova never edits its own code: an update only installs commits from the owner's repo
(`update.remote` / `update.branch` in config/default.yaml).
