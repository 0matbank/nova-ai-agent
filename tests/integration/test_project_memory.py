"""Phase 17 pass — "Project context persists between tasks": what one task did on a
project reaches the next task on that project (and only that project), survives
a restart, and never holds a secret (plan §20, §21, §49.2, §51, §58)."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from core.agents import Roster
from core.config import load_config
from core.db.engine import make_sessionmaker
from core.db.migrate import check_and_migrate
from core.memory import ProjectMemory, SecretRefused
from core.orchestrator.executor import UserRequestExecutor
from core.queue.states import TaskState
from core.router.intent import IntentRouter
from models.router import ProviderRouter
from providers.provider_base import HealthState
from tests.mocks.providers import FakeAdapter
from tests.mocks.skills import SkillEnv, make_skill_env

GIT = shutil.which("git") or "git"


@pytest.fixture
def mem(tmp_path: Path) -> ProjectMemory:
    res = check_and_migrate(tmp_path / "m.db", tmp_path / "bk")
    return ProjectMemory(make_sessionmaker(res.engine))


def test_entries_are_scoped_upserted_and_rolled_off(mem: ProjectMemory) -> None:
    mem.put("click-tv", "decision", "Always keep the HLS player")
    mem.put("stream-doctor", "fact", "Uses ffprobe for checks")
    a = mem.put("click-tv", "known_issue", "tests fail: x", key="tests")
    b = mem.put("click-tv", "known_issue", "tests fail: y", key="tests")    # same topic
    assert a == b and [e.content for e in mem.entries("click-tv", ("known_issue",))] == \
        ["tests fail: y"]
    assert {e.project for e in mem.entries("click-tv")} == {"click-tv"}    # §58 rule 13
    assert "ffprobe" not in mem.context("click-tv")[0] + mem.context("click-tv")[1]
    for i in range(35):
        mem.put("click-tv", "task", f"Task #{i}: did {i}", key=f"task:{i}")
    tasks = mem.entries("click-tv", ("task",))
    assert len(tasks) == 30 and tasks[0].content == "Task #34: did 34"   # newest kept
    assert mem.resolve("click-tv", "known_issue", "tests")
    assert mem.entries("click-tv", ("known_issue",)) == []
    assert mem.forget("click-tv", tasks[0].id) and not mem.forget("stream-doctor", tasks[1].id)


def test_secrets_never_enter_memory(mem: ProjectMemory) -> None:
    key = "AIza" + "K" * 35
    with pytest.raises(SecretRefused):
        mem.put("click-tv", "fact", f"the api key is {key}")             # owner text: refused
    mem.put("click-tv", "task", f"Task #1: config had {key}", key="task:1")  # Nova: masked
    stored = mem.entries("click-tv")[0].content
    assert key not in stored and "REDACTED" in stored


def test_context_fits_its_budget_and_puts_decisions_first(mem: ProjectMemory) -> None:
    mem.put("click-tv", "decision", "Never change the scanner timeout")
    for i in range(60):
        mem.put("click-tv", "task", f"Task #{i}: " + "x" * 100, key=f"task:{i}")
    remembered, decisions = mem.context("click-tv", budget=600)
    assert decisions == "- Never change the scanner timeout"
    assert len(remembered) <= 600 and "Task #59" in remembered


# ------------------------------------------------------------- through the engine

def make_repo(folder: Path, good: bool) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    if not (folder / ".git").exists():
        subprocess.run([GIT, "init", "-q", "-b", "main", str(folder)], check=True)  # noqa: S603
        for k, v in (("user.name", "t"), ("user.email", "t@t")):    # Nova commits here
            subprocess.run([GIT, "-C", str(folder), "config", k, v], check=True)  # noqa: S603
    (folder / "calc.py").write_text(
        "def add(a, b):\n    return a " + ("+" if good else "-") + " b\n", encoding="utf-8")
    (folder / "test_calc.py").write_text(
        "import unittest\nfrom calc import add\n\n\nclass T(unittest.TestCase):\n"
        "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n", encoding="utf-8")


def register(s: SkillEnv, local: FakeAdapter, codex: FakeAdapter | None = None
             ) -> ProjectMemory:
    """A fresh executor over the same DB — exactly what a service restart builds."""
    real = load_config()
    adapters = {n: FakeAdapter(n, state=HealthState.UNAVAILABLE) for n in real.providers.providers}
    adapters["ollama_local"] = local
    if codex is not None:
        adapters["openai_codex"] = codex
    router = ProviderRouter(real.providers, adapters)
    memory = ProjectMemory(s.tasks.store._sessions)
    s.tasks.engine.register("user_request", UserRequestExecutor(
        IntentRouter(router), router, s.runner, Roster.load(s.runner.env.config), memory))
    return memory


def setup(tmp_path: Path, repo: Path, local: list | None = None,
          codex: FakeAdapter | None = None) -> tuple[SkillEnv, FakeAdapter, ProjectMemory]:
    s = make_skill_env(tmp_path)
    (tmp_path / "config" / "projects" / "git-demo.yaml").write_text(
        f"name: Git Demo\nlocal_folder: '{repo}'\ntest_command: \"python -m unittest -v\"\n"
        "aliases: [gitdemo]\n", encoding="utf-8")
    s.runner.env.config = load_config(tmp_path / "config", tmp_path / "rt")
    local_ad = FakeAdapter("ollama_local", local or [])
    return s, local_ad, register(s, local_ad, codex)


def submit(s: SkillEnv, text: str) -> int:
    tid = int(s.tasks.store.create(title=text, request_text=text, task_type="user_request",
                                   channel="telegram", chat_id="555").id)
    for _ in range(4):
        if not asyncio.run(s.tasks.engine.run_once()):
            break
    return tid


def test_project_context_persists_between_tasks(tmp_path: Path) -> None:
    repo = tmp_path / "demo"
    make_repo(repo, good=False)
    s, local, memory = setup(tmp_path, repo, local=["Tests fail in calc.add; HLS rule noted."])
    # task 1: the tests fail → an open issue in Git Demo's memory
    t1 = submit(s, "Git Demo test koro")
    assert s.tasks.store.get(t1).state is TaskState.COMPLETED
    (issue,) = memory.entries("git-demo", ("known_issue",))
    assert issue.task_id == t1 and "Tests fail" in issue.content
    # task 2: the owner's decision for the project
    t2 = submit(s, "git demo te mone rakho: sobsomoy type hints use korbe")
    t = s.tasks.store.get(t2)
    assert t.state is TaskState.COMPLETED and "সিদ্ধান্ত" in t.result_summary
    (dec,) = memory.entries("git-demo", ("decision",))
    assert dec.content == "sobsomoy type hints use korbe" and dec.source == "owner"
    # task 3 — a NEW executor (as after a restart) — sees both, no other project's notes
    memory.put("click-tv", "decision", "Click TV only rule")
    local2 = FakeAdapter("ollama_local", ["ok"])
    register(s, local2)                                  # "restart": new executor, same DB
    t3 = submit(s, "git demo project er ki obostha?")
    assert s.tasks.store.get(t3).state is TaskState.COMPLETED
    ctx = local2.requests[-1].context
    assert "## Relevant Memory" in ctx and "OPEN ISSUE" in ctx and "Tests fail" in ctx
    assert "## Previous Decisions" in ctx and "type hints" in ctx
    assert "Click TV only rule" not in ctx
    # task 4: the tests pass again → the open issue is resolved, the run is remembered
    make_repo(repo, good=True)
    submit(s, "Git Demo test koro")
    assert memory.entries("git-demo", ("known_issue",)) == []
    assert any("tests passed" in e.content for e in memory.entries("git-demo", ("task",)))


def test_agents_and_memory_commands(mem: ProjectMemory) -> None:
    from datetime import UTC, datetime

    from channels.base import IncomingMessage
    from core.orchestrator.agent_commands import AgentCommands
    cfg = load_config()
    cmds = AgentCommands(cfg, Roster.load(cfg), mem, "Asia/Dhaka")

    def msg(text: str) -> IncomingMessage:
        return IncomingMessage("telegram", "555", "1", "9", text, datetime.now(UTC))
    agents = asyncio.run(cmds.agents(msg("/agents"), []))
    assert "Windows Operator" in agents and "Click TV Specialist" in agents
    assert "seo — profile নেই" in agents
    entry = mem.put("git-demo", "decision", "Always run the tests")
    out = asyncio.run(cmds.memory_cmd(msg("/memory git demo"), ["git", "demo"]))
    assert f"#{entry}" in out and "Always run the tests" in out
    gone = asyncio.run(cmds.memory_cmd(msg(""), ["git", "demo", "forget", str(entry)]))
    assert "মুছে" in gone and mem.entries("git-demo") == []
    assert "নামে project নেই" in asyncio.run(cmds.memory_cmd(msg(""), ["nothing"]))


def test_knowledge_files_are_read_and_empty_templates_skipped(tmp_path: Path) -> None:
    from core.agents.roster import ProjectAgent
    from core.config.schema import ProjectProfile
    kd = tmp_path / "project-knowledge" / "demo"
    kd.mkdir(parents=True)
    (kd / "rules.md").write_text("# demo — Rules\n\n<!-- template note -->\n", encoding="utf-8")
    (kd / "architecture.md").write_text("# Architecture\nPlayer: HLS.js in web/player.js\n",
                                        encoding="utf-8")
    (kd / "decisions.md").write_text("- 2026-10: no ads before the stream\n", encoding="utf-8")
    p = ProjectAgent("demo", "Demo Specialist", "Knows demo.",
                     ProjectProfile(name="Demo", knowledge_dir="project-knowledge/demo"))
    rules = p.rules(tmp_path)
    assert "Architecture:\nPlayer: HLS.js" in rules and "Decisions:\n- 2026-10" in rules
    assert "Rules:" not in rules and "template note" not in rules


def test_a_secret_in_a_note_is_refused(tmp_path: Path) -> None:
    repo = tmp_path / "demo"
    make_repo(repo, good=True)
    s, _, memory = setup(tmp_path, repo)
    tid = submit(s, "git demo te mone rakho: token holo ghp_" + "a" * 36)
    assert "🔒" in s.tasks.store.get(tid).result_summary
    assert memory.entries("git-demo") == []


def test_coding_gets_the_projects_decisions_and_memory(tmp_path: Path) -> None:
    repo = tmp_path / "demo"
    make_repo(repo, good=False)
    subprocess.run([GIT, "-C", str(repo), "add", "-A"], check=True)  # noqa: S603
    subprocess.run([GIT, "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",  # noqa: S603
                    "commit", "-qm", "base"], check=True)
    def fix(_: object) -> str:
        make_repo(repo, good=True)
        return "Fixed add()."
    codex = FakeAdapter("openai_codex", [fix])
    s, _, memory = setup(tmp_path, repo, codex=codex)
    memory.put("git-demo", "decision", "Keep add() pure — no printing", project_name="Git Demo")
    memory.put("git-demo", "known_issue", "Tests fail: add(2,3) == -1", key="tests")
    tid = submit(s, "git demo te add function er bug fix koro")
    assert s.tasks.store.get(tid).state is TaskState.COMPLETED
    system = codex.requests[0].system
    assert "Keep add() pure" in system and "Tests fail: add(2,3)" in system
    # the finished coding task is remembered, and the open issue is closed
    assert memory.entries("git-demo", ("known_issue",)) == []
    (task,) = memory.entries("git-demo", ("task",))
    assert task.task_id == tid and "calc.py" in task.content and "tests passed" in task.content
