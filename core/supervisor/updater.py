"""Apply an approved update with automatic rollback (Phase 23).

    check → DB backup → fast-forward → uv sync → preflight → start → health
    any failure → stop → git reset to the old commit → uv sync → DB restore (if the
    schema changed) → start the old version → health

The result goes to <run>/update-result.json for the /update task (which resumes after
the restart). "pending_health" while the new version proves itself; then "updated"
or "rolled_back".
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.lifecycle import UPDATE_RESULT, git, head_commit, read_json, write_json

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
Log = Callable[[str], None]


def run(argv: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
    try:
        r = subprocess.run(argv, cwd=cwd, capture_output=True, timeout=timeout,  # noqa: S603
                           creationflags=NO_WINDOW, check=False,
                           env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, f"{type(e).__name__}: {e}"
    return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace")


def schema_revision(db: Path) -> str | None:
    if not db.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
        try:
            row = con.execute("SELECT version_num FROM alembic_version").fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    return str(row[0]) if row else None


def backup_db(db: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
    out = sqlite3.connect(dest)
    try:
        src.backup(out)                    # consistent copy even with WAL files around
    finally:
        out.close()
        src.close()


def restore_db(backup: Path, db: Path) -> None:
    for suffix in ("-wal", "-shm"):
        Path(str(db) + suffix).unlink(missing_ok=True)
    shutil.copy2(backup, db)


@dataclass
class Plan:
    app_dir: Path
    python: Path                           # the venv's python.exe
    uv: str
    database: Path
    backups_dir: Path
    run_dir: Path
    task_id: int
    from_commit: str
    to_commit: str
    remote: str
    branch: str
    health_timeout: float = 180
    stable_seconds: float = 20
    run_unit_tests: bool = True


@dataclass
class Hooks:
    """How the updater stops/starts the children (the supervisor; tests fake it)."""
    stop_all: Callable[[], None]
    start_all: Callable[[], None]
    healthy: Callable[[str, float, float], tuple[bool, str]]   # commit, timeout, stable
    log: Log = print
    run: Callable[[list[str], Path, int], tuple[int, str]] = run


@dataclass
class Outcome:
    status: str = "pending"
    failed_step: str = ""
    reason: str = ""
    db_backup: str = ""
    db_restored: bool = False
    rollback_ok: bool | None = None
    steps: list[str] = field(default_factory=list)


def _result(plan: Plan, out: Outcome) -> None:
    write_json(plan.run_dir / UPDATE_RESULT, {
        "task_id": plan.task_id, "status": out.status, "from": plan.from_commit,
        "to": plan.to_commit, "failed_step": out.failed_step, "reason": out.reason[:1500],
        "db_backup": out.db_backup, "db_restored": out.db_restored,
        "rollback_ok": out.rollback_ok, "steps": out.steps, "at": time.time()})


def apply_update(plan: Plan, hooks: Hooks) -> Outcome:
    out = Outcome()
    rev_before = schema_revision(plan.database)
    app, py = plan.app_dir, str(plan.python)

    def step(name: str) -> None:
        out.steps.append(name)
        hooks.log(f"update: {name}")

    def fail(name: str, why: str) -> Outcome:
        out.failed_step, out.reason = name, why.strip()[-1500:]
        hooks.log(f"update FAILED at {name}: {out.reason[:300]}")
        return rollback(plan, hooks, out, rev_before)

    step("check")
    if head_commit(app) != plan.from_commit:
        out.failed_step, out.reason, out.status = "check", "HEAD moved since approval", \
            "rolled_back"
        out.rollback_ok = True
        hooks.start_all()
        _result(plan, out)
        return out
    rc, dirty = git(app, "status", "--porcelain", "--untracked-files=no")
    if rc != 0 or dirty:
        out.failed_step, out.reason, out.status = "check", f"working tree not clean: {dirty}", \
            "rolled_back"
        out.rollback_ok = True
        hooks.start_all()
        _result(plan, out)
        return out
    git(app, "fetch", "--quiet", plan.remote, plan.branch, timeout=300)
    rc, _ = git(app, "merge-base", "--is-ancestor", plan.from_commit, plan.to_commit)
    if rc != 0:
        return fail("check", "the approved commit is not a fast-forward of the current one")

    step("backup")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = plan.backups_dir / f"pre-update-{stamp}-{plan.from_commit[:8]}.db"
    try:
        if plan.database.is_file():
            backup_db(plan.database, backup)
            out.db_backup = str(backup)
    except (OSError, sqlite3.Error) as e:
        hooks.start_all()                  # nothing changed yet: just come back
        out.failed_step, out.reason, out.status, out.rollback_ok = "backup", str(e), \
            "rolled_back", True
        _result(plan, out)
        return out

    step("checkout")
    rc, text = git(app, "merge", "--ff-only", plan.to_commit)
    if rc != 0:
        return fail("checkout", text)
    step("dependencies")
    rc, text = hooks.run([plan.uv, "sync", "--frozen", "--all-extras"], app, 1200)
    if rc != 0:
        return fail("dependencies", text)
    step("preflight")
    rc, text = hooks.run([py, "-m", "core.bootstrap", "--check"], app, 300)
    if rc != 0:
        return fail("preflight", text)
    if plan.run_unit_tests:
        rc, text = hooks.run([py, "-m", "pytest", "-q", "-x", "-p", "no:randomly",
                              "tests/unit"], app, 900)
        if rc != 0:
            return fail("unit tests", text[-1500:])
    step("start")
    out.status = "pending_health"
    _result(plan, out)
    hooks.start_all()
    ok, why = hooks.healthy(plan.to_commit, plan.health_timeout, plan.stable_seconds)
    if not ok:
        hooks.stop_all()
        return fail("health", why)
    out.status = "updated"
    _result(plan, out)
    hooks.log(f"update OK: {plan.from_commit[:8]} → {plan.to_commit[:8]}")
    return out


def rollback(plan: Plan, hooks: Hooks, out: Outcome, rev_before: str | None) -> Outcome:
    app = plan.app_dir
    out.status = "rolled_back"
    problems = []
    if head_commit(app) != plan.from_commit:
        rc, text = git(app, "reset", "--hard", plan.from_commit)
        if rc != 0:
            problems.append(f"git reset: {text[:200]}")
    rc, text = hooks.run([plan.uv, "sync", "--frozen", "--all-extras"], app, 1200)
    if rc != 0:
        problems.append(f"uv sync: {text[-200:]}")
    if out.db_backup and schema_revision(plan.database) != rev_before:
        try:
            restore_db(Path(out.db_backup), plan.database)
            out.db_restored = True
        except OSError as e:
            problems.append(f"DB restore: {e}")
    _result(plan, out)                     # the old core reads this when its task resumes
    hooks.start_all()
    ok, why = hooks.healthy(plan.from_commit, plan.health_timeout, plan.stable_seconds)
    if not ok:
        problems.append(f"old version health: {why}")
    out.rollback_ok = not problems
    if problems:
        out.reason += "\nROLLBACK PROBLEMS: " + "; ".join(problems)
    _result(plan, out)
    hooks.log("rollback " + ("OK" if out.rollback_ok else "WITH PROBLEMS: " + "; ".join(problems)))
    return out


def read_request(run_dir: Path) -> dict[str, Any] | None:
    from core.lifecycle import UPDATE_REQUEST
    req = read_json(run_dir / UPDATE_REQUEST)
    (run_dir / UPDATE_REQUEST).unlink(missing_ok=True)       # one attempt per approval
    return req
