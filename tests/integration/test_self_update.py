"""Phase 23: graceful shutdown, restart, and /update with automatic rollback.

The updater runs on throw-away git repos (a "remote" + an "app" clone) and a real
SQLite DB; uv sync / preflight / start / health are faked per scenario. The supervisor's
children are real processes. Nothing here touches Nova's own code folder."""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from core.lifecycle import (
    EXIT_RESTART,
    UPDATE_REQUEST,
    UPDATE_RESULT,
    head_commit,
    read_json,
    write_json,
)
from core.supervisor.process import Child
from core.supervisor.updater import Hooks, Plan, apply_update, schema_revision

GIT = shutil.which("git") or "git"


def git(repo: Path, *args: str) -> str:
    return subprocess.run([GIT, "-C", str(repo), *args], check=True, capture_output=True,  # noqa: S603
                          text=True).stdout.strip()


def commit(repo: Path, name: str, text: str) -> str:
    (repo / name).write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", f"change {name}")
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repos(tmp_path: Path) -> tuple[Path, Path, str]:
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    app = tmp_path / "app"
    subprocess.run([GIT, "init", "-q", "--bare", "-b", "main", str(remote)], check=True)  # noqa: S603
    subprocess.run([GIT, "clone", "-q", str(remote), str(work)], check=True,  # noqa: S603
                   capture_output=True)
    for repo in (work,):
        git(repo, "config", "user.name", "t")
        git(repo, "config", "user.email", "t@example.invalid")
    base = commit(work, "app.txt", "v1")
    git(work, "push", "-q", "origin", "HEAD:main")
    subprocess.run([GIT, "clone", "-q", str(remote), str(app)], check=True,  # noqa: S603
                   capture_output=True)
    return work, app, base


def make_db(path: Path, revision: str = "005") -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE alembic_version (version_num TEXT)")
    con.execute("INSERT INTO alembic_version VALUES (?)", (revision,))
    con.execute("CREATE TABLE tasks (id INTEGER)")
    con.commit()
    con.close()


class Fake:
    """Children + commands for one scenario."""

    def __init__(self, app: Path, db: Path, bad: str | None = None,
                 migrate_to: str | None = None) -> None:
        self.app, self.db = app, db
        self.bad, self.migrate_to = bad, migrate_to
        self.calls: list[str] = []
        self.running = False

    def run(self, argv: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
        what = "uv sync" if "sync" in argv else ("tests" if "pytest" in argv else "preflight")
        if what == "uv sync":
            # a plain `uv sync --frozen` would UNINSTALL the voice/desktop extras
            assert argv[1:] == ["sync", "--frozen", "--all-extras"], argv
        self.calls.append(f"{what}@{head_commit(self.app)[:7]}")
        if self.bad == what and (self.app / "broken.txt").exists():
            return 1, f"{what} failed: config invalid"
        return 0, "ok"

    def start_all(self) -> None:
        self.running = True
        self.calls.append(f"start@{head_commit(self.app)[:7]}")
        if self.migrate_to and (self.app / "broken.txt").exists():
            con = sqlite3.connect(self.db)        # the new version migrates + writes data
            con.execute("UPDATE alembic_version SET version_num = ?", (self.migrate_to,))
            con.execute("INSERT INTO tasks VALUES (99)")
            con.commit()
            con.close()

    def stop_all(self) -> None:
        self.running = False
        self.calls.append("stop")

    def healthy(self, commit_: str, timeout: float, stable: float) -> tuple[bool, str]:
        self.calls.append(f"health@{commit_[:7]}")
        if self.bad == "health" and (self.app / "broken.txt").exists():
            return False, "core exited with code 1 while starting"
        return head_commit(self.app) == commit_, "up"

    def hooks(self) -> Hooks:
        return Hooks(stop_all=self.stop_all, start_all=self.start_all, healthy=self.healthy,
                     log=lambda s: None, run=self.run)


def plan(tmp: Path, app: Path, db: Path, frm: str, to: str) -> Plan:
    return Plan(app_dir=app, python=Path(sys.executable), uv="uv", database=db,
                backups_dir=tmp / "backups", run_dir=tmp / "run", task_id=7,
                from_commit=frm, to_commit=to, remote="origin", branch="main",
                health_timeout=5, stable_seconds=1)


def test_good_update_is_installed(tmp_path: Path, repos) -> None:  # type: ignore[no-untyped-def]
    work, app, base = repos
    new = commit(work, "app.txt", "v2")
    git(work, "push", "-q", "origin", "HEAD:main")
    db = tmp_path / "agent.db"
    make_db(db)
    fake = Fake(app, db)
    out = apply_update(plan(tmp_path, app, db, base, new), fake.hooks())
    assert out.status == "updated" and head_commit(app) == new
    assert (app / "app.txt").read_text() == "v2"
    assert Path(out.db_backup).is_file() and fake.running
    res = read_json(tmp_path / "run" / UPDATE_RESULT)
    assert res is not None and res["status"] == "updated" and res["task_id"] == 7


@pytest.mark.parametrize("bad", ["preflight", "tests", "uv sync"])
def test_failed_preflight_rolls_back(tmp_path: Path, repos, bad: str) -> None:  # type: ignore[no-untyped-def]
    work, app, base = repos
    (work / "broken.txt").write_text("x")
    new = commit(work, "app.txt", "v2-broken")
    git(work, "push", "-q", "origin", "HEAD:main")
    db = tmp_path / "agent.db"
    make_db(db)
    fake = Fake(app, db, bad=bad)
    out = apply_update(plan(tmp_path, app, db, base, new), fake.hooks())
    assert out.status == "rolled_back" and out.rollback_ok is True
    assert head_commit(app) == base and not (app / "broken.txt").exists()
    assert (app / "app.txt").read_text() == "v1"
    assert fake.calls[-2:] == [f"start@{base[:7]}", f"health@{base[:7]}"]  # old one back
    assert fake.calls.count(f"uv sync@{base[:7]}") == 1                    # deps restored
    assert not out.db_restored                                             # schema unchanged
    res = read_json(tmp_path / "run" / UPDATE_RESULT)
    assert res is not None and res["status"] == "rolled_back" and res["failed_step"] in (
        bad, "unit tests", "dependencies")


def test_new_version_that_dies_after_migrating_is_rolled_back_with_the_db(  # type: ignore[no-untyped-def]
        tmp_path: Path, repos) -> None:
    work, app, base = repos
    (work / "broken.txt").write_text("x")
    new = commit(work, "app.txt", "v2-crashes")
    git(work, "push", "-q", "origin", "HEAD:main")
    db = tmp_path / "agent.db"
    make_db(db, "005")
    fake = Fake(app, db, bad="health", migrate_to="006")
    out = apply_update(plan(tmp_path, app, db, base, new), fake.hooks())
    assert out.status == "rolled_back" and out.failed_step == "health"
    assert head_commit(app) == base and out.db_restored and out.rollback_ok
    assert schema_revision(db) == "005"                                   # migration undone
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    con.close()
    assert "stop" in fake.calls and fake.running


def test_moved_head_or_dirty_tree_changes_nothing(tmp_path: Path, repos) -> None:  # type: ignore[no-untyped-def]
    work, app, base = repos
    new = commit(work, "app.txt", "v2")
    git(work, "push", "-q", "origin", "HEAD:main")
    db = tmp_path / "agent.db"
    make_db(db)
    fake = Fake(app, db)
    out = apply_update(plan(tmp_path, app, db, "0" * 40, new), fake.hooks())
    assert out.status == "rolled_back" and out.failed_step == "check" and head_commit(app) == base
    (app / "app.txt").write_text("local edit")
    out = apply_update(plan(tmp_path, app, db, base, new), fake.hooks())
    assert out.failed_step == "check" and (app / "app.txt").read_text() == "local edit"


# ------------------------------------------------------------- /update check

def test_check_update(tmp_path: Path, repos) -> None:  # type: ignore[no-untyped-def]
    from core.orchestrator.self_update import check_update
    work, app, base = repos
    assert check_update(app, "origin", "main") == {"ok": True, "from": base, "to": base,
                                                    "commits": []}
    new = commit(work, "app.txt", "v2")
    git(work, "push", "-q", "origin", "HEAD:main")
    info = check_update(app, "origin", "main")
    assert info["ok"] and info["to"] == new and len(info["commits"]) == 1
    assert head_commit(app) == base                        # checking never installs
    (app / "app.txt").write_text("dirty")
    assert not check_update(app, "origin", "main")["ok"]
    git(app, "checkout", "--", "app.txt")
    git(work, "reset", "-q", "--hard", base)               # history rewritten upstream
    commit(work, "other.txt", "x")
    git(work, "push", "-q", "-f", "origin", "HEAD:main")
    git(app, "config", "user.name", "t")
    git(app, "config", "user.email", "t@example.invalid")
    commit(app, "local.txt", "local only")
    assert "fast-forward" in check_update(app, "origin", "main")["why"]


# --------------------------------------------- the /update task end to end

def test_update_task_hands_over_and_reports_after_restart(tmp_path: Path, repos,  # type: ignore[no-untyped-def]
                                                          monkeypatch) -> None:
    from core.orchestrator.self_update import SELF_UPDATE, SelfUpdateExecutor
    from tests.mocks.skills import make_skill_env
    work, app, base = repos
    new = commit(work, "app.txt", "v2")
    git(work, "push", "-q", "origin", "HEAD:main")
    monkeypatch.setenv("NOVA_SUPERVISED", "1")
    s = make_skill_env(tmp_path)
    exits: list[int] = []
    ex = SelfUpdateExecutor(s.cfg, lambda code, why: exits.append(code), app_dir=app)
    s.tasks.engine.register(SELF_UPDATE, ex)
    tid = int(s.tasks.store.create(title="u", request_text="/update", task_type=SELF_UPDATE,
                                   channel="telegram", chat_id="555").id)
    asyncio.run(s.tasks.engine.run_once())                 # → approval
    assert s.tasks.store.get(tid).state.value == "WAITING_APPROVAL"
    nonce = s.tasks.last_callback(True).split(":")[1]
    s.tasks.approvals.decide(nonce, True, "555")
    asyncio.run(s.tasks.engine.run_once())                 # → request + going offline
    run = s.cfg.path("data_dir") / "run"
    req = read_json(run / UPDATE_REQUEST)
    assert exits == [76] and req is not None and req["to"] == new and req["task_id"] == tid
    t = s.tasks.store.get(tid)
    assert t.state.value == "RETRYING" and t.error_code == "INTERRUPTED_BY_RESTART"
    # … the supervisor installs it and the new core resumes the same task
    git(app, "merge", "-q", "--ff-only", new)
    write_json(run / UPDATE_RESULT, {"task_id": tid, "status": "updated", "from": base,
                                     "to": new})
    asyncio.run(s.tasks.engine.run_once())
    t = s.tasks.store.get(tid)
    assert t.state.value == "COMPLETED" and "update হয়েছে" in (t.result_summary or "")
    assert exits == [76]                                   # not requested a second time
    assert any("PASS" in m for _, m in s.tasks.sent)


def test_update_refuses_without_supervisor(tmp_path: Path, monkeypatch) -> None:
    from core.orchestrator.self_update import NOT_SUPERVISED, LifecycleCommands
    monkeypatch.delenv("NOVA_SUPERVISED", raising=False)
    cmds = LifecycleCommands(None, None, lambda c, r: None)
    from channels.base import IncomingMessage
    msg = IncomingMessage(channel="telegram", chat_id="555", user_id="555", message_id="1",
                          text="/update", received_at=datetime.now(UTC))
    assert asyncio.run(cmds.update(msg, [])) == NOT_SUPERVISED
    assert asyncio.run(cmds.restart(msg, [])) == NOT_SUPERVISED


def test_restart_command_and_telegram_menu(monkeypatch) -> None:
    from core.orchestrator.commands import CommandRouter, HealthSources
    from core.orchestrator.self_update import LifecycleCommands
    monkeypatch.setenv("NOVA_SUPERVISED", "1")
    asked: list[int] = []
    cmds = LifecycleCommands(None, None, lambda c, r: asked.append(c))
    router = CommandRouter(Path("."), HealthSources(), "Nova")
    router.add("/restart-agent", cmds.restart)
    from channels.base import IncomingMessage
    for text in ("/restart-agent", "/restart_agent"):
        msg = IncomingMessage(channel="telegram", chat_id="555", user_id="555", message_id="1",
                              text=text, received_at=datetime.now(UTC))
        reply = asyncio.run(router.handle(msg))
        assert reply is not None and "restart" in reply.text
    assert asked == [EXIT_RESTART, EXIT_RESTART]
    names = [c for c, _ in router.menu()]
    assert "/restart_agent" in names and all("-" not in n for n in names)


# ------------------------------------------------------ graceful shutdown

def test_running_task_stops_at_a_checkpoint_and_resumes(tmp_path: Path) -> None:
    from core.queue.engine import TaskContext, Verdict, VerdictStatus
    from tests.mocks.tasks import make_task_env
    env = make_task_env(tmp_path / "a.db", tmp_path / "bk")
    runs: list[str] = []

    class Slow:
        async def plan(self, ctx: TaskContext) -> list[str]:
            return ["one", "two"]

        async def run(self, ctx: TaskContext) -> str:
            async def one(prev: Any) -> dict[str, Any]:
                runs.append("one")
                return {"done": 1}
            await ctx.run_step(1, one)
            env.engine.draining = True                     # Nova starts going offline here
            await ctx.checkpoint()

            async def two(prev: Any) -> dict[str, Any]:
                runs.append("two")
                return {"done": 2}
            await ctx.run_step(2, two)
            return "finished"

        async def verify(self, ctx: TaskContext, result: str) -> Verdict:
            return Verdict(True, "", VerdictStatus.PASS)
    env.engine.register("slow", Slow())
    tid = int(env.store.create(title="s", request_text="s", task_type="slow",
                               channel="telegram", chat_id="555").id)
    asyncio.run(env.engine.run_once())
    t = env.store.get(tid)
    assert t.state.value == "RETRYING" and t.error_code == "INTERRUPTED_BY_RESTART"
    assert t.retry_count == 0 and env.engine.interrupted_ids == [tid] and runs == ["one"]
    assert not asyncio.run(env.engine.run_once())          # draining: nothing new starts
    env.engine.draining = False                            # … the restarted engine
    env.engine.register("slow", type("Again", (Slow,), {
        "run": lambda self, ctx: _second_half(ctx, runs)})())
    asyncio.run(env.engine.run_once())
    assert env.store.get(tid).state.value == "COMPLETED" and runs == ["one", "two"]


async def _second_half(ctx: Any, runs: list[str]) -> str:
    async def one(prev: Any) -> dict[str, Any]:
        runs.append("one-again")                           # must NOT run: step 1 is DONE
        return {}

    async def two(prev: Any) -> dict[str, Any]:
        runs.append("two")
        return {}
    await ctx.run_step(1, one)
    await ctx.run_step(2, two)
    return "finished"


# ------------------------------------------------- supervisor child processes

CHILD = """
import os, sys, time, pathlib
stop = pathlib.Path(os.environ["NOVA_STOP_FILE"])
mode = sys.argv[1]
if mode == "crash":
    sys.exit(1)
while True:
    if stop.exists() and mode == "polite":
        pathlib.Path(sys.argv[2]).write_text("graceful")
        sys.exit(0)
    time.sleep(0.1)
"""


def test_children_stop_gracefully_or_get_killed(tmp_path: Path) -> None:
    script = tmp_path / "child.py"
    script.write_text(CHILD, encoding="utf-8")
    marker = tmp_path / "graceful.txt"
    polite = Child("polite", [sys.executable, str(script), "polite", str(marker)], tmp_path,
                   tmp_path / "run", tmp_path / "logs")
    stubborn = Child("stubborn", [sys.executable, str(script), "stubborn"], tmp_path,
                     tmp_path / "run", tmp_path / "logs")
    (tmp_path / "run").mkdir()
    for c in (polite, stubborn):
        c.start()
    time.sleep(0.5)
    assert polite.running and stubborn.running
    assert polite.stop(10) == 0 and marker.read_text() == "graceful"
    t0 = time.time()
    stubborn.stop(1)
    assert not stubborn.running and time.time() - t0 < 10        # killed after the timeout
    assert os.environ.get("NOVA_STOP_FILE") is None              # nothing leaked into tests


def test_crashing_child_backs_off_and_alerts_once(tmp_path: Path, monkeypatch) -> None:
    from core.supervisor.supervisor import Supervisor
    settings = {"data_dir": str(tmp_path / "data"), "logs_dir": str(tmp_path / "logs"),
                "secrets_dir": str(tmp_path / "secrets"), "desktop": False, "browser": False,
                "graceful_timeout": 5, "worker_kill_after": 2}
    sup = Supervisor(tmp_path, settings, Path(sys.executable))
    alerts: list[str] = []
    monkeypatch.setattr(sup, "alert", alerts.append)
    core = sup.child("core")
    delays = []
    for _ in range(4):
        before = time.time()
        sup.crashed(core, 1)
        delays.append(round(core.next_start - before))
    assert delays == [2, 5, 15, 60] and len(alerts) == 1
    sup.crashed(core, 2)                                         # bad config: slow down
    assert core.next_start - time.time() > 250


# ---------------------------------- live-test regression (2026-10-16): the supervisor
# died printing "→" to a cp1252 console right after a successful update

def test_supervisor_survives_logging_and_event_errors(tmp_path: Path, monkeypatch) -> None:
    from core.supervisor.supervisor import Supervisor

    def cp1252_console(line: str) -> None:
        line.encode("cp1252")                       # raises on "→"
    settings = {"data_dir": str(tmp_path / "data"), "logs_dir": str(tmp_path / "logs"),
                "secrets_dir": str(tmp_path / "s"), "desktop": False, "browser": False}
    sup = Supervisor(tmp_path, settings, Path(sys.executable), log=cp1252_console)
    sup.log("update OK: 0bf77642 → 80b1df79")      # must not raise
    assert "→" in (tmp_path / "logs" / "supervisor" / "supervisor.log").read_text("utf-8")
    monkeypatch.setattr(sup, "start_all", lambda: None)
    monkeypatch.setattr(sup, "stop_all", lambda: None)
    ticks = []

    def tick() -> None:
        ticks.append(1)
        if len(ticks) == 1:
            raise RuntimeError("one bad event")
        sup.stopping.set()
    monkeypatch.setattr(sup, "tick", tick)
    monkeypatch.setattr(sup.stopping, "wait", lambda t=None: sup.stopping.is_set())
    assert sup.run() == 0 and len(ticks) == 2       # kept going after the error
    assert "one bad event" in (tmp_path / "logs" / "supervisor" / "supervisor.log").read_text(
        "utf-8")


def test_supervised_means_the_supervisor_is_still_alive(monkeypatch) -> None:
    from core.lifecycle import supervised
    monkeypatch.setenv("NOVA_SUPERVISED", "1")
    monkeypatch.setenv("NOVA_SUPERVISOR_PID", str(os.getpid()))
    assert supervised()
    dead = subprocess.Popen([sys.executable, "-c", "pass"])  # noqa: S603
    dead.wait()
    monkeypatch.setenv("NOVA_SUPERVISOR_PID", str(dead.pid))
    assert not supervised()                          # /update would strand Nova offline
