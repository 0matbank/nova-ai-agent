"""Phase 16 through the real task engine: the chosen specialist does the work with
only its own skills, project specialists bring their project's context, and the
owner sees which agent did it."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

from core.config import load_config
from core.orchestrator.executor import UserRequestExecutor
from core.queue.states import TaskState
from core.router.intent import IntentRouter
from models.router import ProviderRouter
from providers.provider_base import HealthState
from tests.mocks.providers import FakeAdapter
from tests.mocks.skills import SkillEnv, make_skill_env

GIT = shutil.which("git") or "git"


def setup(tmp_path: Path, local: list | None = None, codex: list | None = None,
          project: Path | None = None) -> tuple[SkillEnv, dict[str, FakeAdapter]]:
    s = make_skill_env(tmp_path)
    if project is not None:
        (tmp_path / "config" / "projects" / "git-demo.yaml").write_text(
            f"name: Git Demo\nlocal_folder: '{project}'\n"
            "test_command: \"python -m unittest -v\"\naliases: [gitdemo]\n", encoding="utf-8")
        s.runner.env.config = load_config(tmp_path / "config", tmp_path / "rt")
    cfg = load_config()
    adapters = {n: FakeAdapter(n, state=HealthState.UNAVAILABLE) for n in cfg.providers.providers}
    adapters["ollama_local"] = FakeAdapter("ollama_local", local or [])
    adapters["openai_codex"] = FakeAdapter("openai_codex", codex or [])
    router = ProviderRouter(cfg.providers, adapters)
    s.tasks.engine.register("user_request",
                            UserRequestExecutor(IntentRouter(router), router, s.runner))
    return s, adapters


def submit(s: SkillEnv, text: str) -> int:
    return int(s.tasks.store.create(title=text, request_text=text, task_type="user_request",
                                    channel="telegram", chat_id="555").id)


def run_all(s: SkillEnv) -> None:
    for _ in range(4):
        if not asyncio.run(s.tasks.engine.run_once()):
            break


def call(skill: str, tool: str, **params: object) -> str:
    return json.dumps({"thought": "t", "action": "call", "skill": skill, "tool": tool,
                       "params": params})


def answer(text: str) -> str:
    return json.dumps({"thought": "t", "action": "answer", "answer": text}, ensure_ascii=False)


def make_repo(folder: Path) -> None:
    folder.mkdir(parents=True)
    subprocess.run([GIT, "init", "-q", "-b", "main", str(folder)], check=True)  # noqa: S603
    for k, v in (("user.name", "t"), ("user.email", "t@t")):
        subprocess.run([GIT, "-C", str(folder), "config", k, v], check=True)  # noqa: S603
    (folder / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (folder / "test_calc.py").write_text(
        "import unittest\nfrom calc import add\n\n\nclass T(unittest.TestCase):\n"
        "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n", encoding="utf-8")
    subprocess.run([GIT, "-C", str(folder), "add", "-A"], check=True)  # noqa: S603
    subprocess.run([GIT, "-C", str(folder), "commit", "-qm", "base"], check=True)  # noqa: S603


def test_file_agent_answers_from_its_own_tool_results(tmp_path: Path) -> None:
    box = tmp_path / "box"
    box.mkdir()
    for n in ("a.pdf", "b.pdf", "c.txt"):
        (box / n).write_text("x", encoding="utf-8")
    s, ad = setup(tmp_path, local=[
        call("files", "list_dir", path=str(box)),
        answer("৫টা pdf আছে"),                         # not in any result → rejected
        answer("ফোল্ডারে 2টা pdf আছে: a.pdf, b.pdf"),
    ])
    tid = submit(s, f"{box} folder e koyta pdf file ache?")
    run_all(s)
    t = s.tasks.store.get(tid)
    assert t.state is TaskState.COMPLETED, t.error_message
    assert t.result_summary == "ফোল্ডারে 2টা pdf আছে: a.pdf, b.pdf"
    plans = ad["ollama_local"].requests
    assert all(r.task_type == "planning" for r in plans)          # agent → kind of AI work
    assert "files.list_dir(" in plans[0].system and "powershell.run" not in plans[0].system
    assert "a.pdf" in plans[1].context and "<tool_output>" in plans[1].context
    assert "rejected: 5" in plans[2].context                       # grounding check worked
    done = [txt for _, txt in s.tasks.sent if "COMPLETE" in txt][0]
    assert "🧭 File & Document" in done                             # owner sees the agent


def test_a_skill_outside_the_agents_list_is_refused_by_the_runner(tmp_path: Path) -> None:
    s, ad = setup(tmp_path, local=[
        call("powershell", "run", command="Remove-Item C:\\x -Recurse"),
        answer("এটা আমার কাজের মধ্যে পড়ে না, তাই করিনি।"),
    ])
    tid = submit(s, "Downloads folder er sob file muche dao")
    run_all(s)
    t = s.tasks.store.get(tid)
    assert t.state is TaskState.COMPLETED
    assert s.tasks.buttons == []                                    # not even asked
    second = ad["ollama_local"].requests[1].context
    assert "may not use powershell.run" in second


def test_stuck_local_planner_escalates_to_the_cloud_planner(tmp_path: Path) -> None:
    """Owner rule 2026-10-15: low confidence locally → Antigravity/Gemini, silently,
    continuing from the steps already done (not repeating them)."""
    box = tmp_path / "box"
    box.mkdir()
    (box / "a.pdf").write_text("x", encoding="utf-8")
    s, ad = setup(tmp_path, local=[
        call("files", "list_dir", path=str(box)),
        json.dumps({"action": "fail", "reason": "not sure"}),
    ])
    agy = FakeAdapter("google_antigravity", [answer("ফোল্ডারে 1টা pdf আছে: a.pdf")])
    ad["google_antigravity"] = agy                  # the router's own adapter table
    tid = submit(s, f"{box} folder e koyta pdf ache?")
    run_all(s)
    t = s.tasks.store.get(tid)
    assert t.state is TaskState.COMPLETED, t.error_message
    assert t.result_summary == "ফোল্ডারে 1টা pdf আছে: a.pdf"
    (req,) = agy.requests
    assert req.task_type == "reasoning" and "a.pdf" in req.context   # earlier step kept
    assert len(ad["ollama_local"].requests) == 2                     # list_dir not repeated


def test_bangla_rewording_never_changes_file_names(tmp_path: Path) -> None:
    """Drill 2026-10-16: the rewording turned "bill.pdf" into "বিল.পিডিএফ"."""
    box = tmp_path / "box"
    box.mkdir()
    (box / "bill.pdf").write_text("x", encoding="utf-8")
    s, ad = setup(tmp_path, local=[
        call("files", "list_dir", path=str(box)),
        answer("There is 1 PDF: bill.pdf"),
    ])
    gemini = FakeAdapter("gemini_api", [
        "ফোল্ডারে 1টি পিডিএফ: বিল.পিডিএফ",               # rewording 1: name transliterated
        "ফোল্ডারে 1টি PDF আছে: bill.pdf",                # rewording 2: name kept
    ], capabilities=frozenset({"summarization"}))
    ad["gemini_api"] = gemini                        # the rewording step's first choice
    tid = submit(s, f"{box} folder e koyta pdf ache?")
    run_all(s)
    t = s.tasks.store.get(tid)
    assert t.state is TaskState.COMPLETED, t.error_message
    assert t.result_summary == "ফোল্ডারে 1টি PDF আছে: bill.pdf"
    assert "Copy these names exactly" in gemini.requests[-1].system


def test_runner_holds_every_call_to_the_agents_skills(tmp_path: Path) -> None:
    import pytest

    from core.queue.engine import TaskContext
    from core.skills.api import PolicyDenied
    s = make_skill_env(tmp_path)
    task = s.tasks.store.get(s.new_task())
    ctx = TaskContext(task, s.tasks.store, s.tasks.engine.notifier, s.tasks.permissions,
                      s.tasks.approvals)
    ctx.agent, ctx.allowed_skills = "Security", frozenset({"files.list_dir", "search"})
    ok = asyncio.run(s.runner.invoke(ctx, "files", "list_dir", {"path": str(tmp_path)}))
    assert ok.ok
    with pytest.raises(PolicyDenied, match="Security agent may not use files.write"):
        asyncio.run(s.runner.invoke(ctx, "files", "write",
                                    {"path": str(tmp_path / "x.txt"), "content": "x"}))
    assert not (tmp_path / "x.txt").exists()


def test_after_approval_the_exact_approved_step_runs_once(tmp_path: Path) -> None:
    victim = tmp_path / "outside" / "old.log"
    victim.parent.mkdir()
    victim.write_text("x", encoding="utf-8")
    s, ad = setup(tmp_path, local=[
        call("files", "delete", path=str(victim)),
        answer("old.log Recycle Bin-এ পাঠানো হয়েছে।"),
    ])
    tid = submit(s, f"{victim} file ta delete koro")
    run_all(s)
    assert s.tasks.store.get(tid).state is TaskState.WAITING_APPROVAL and victim.exists()
    nonce = s.tasks.last_callback(True).split(":")[1]
    s.tasks.approvals.decide(nonce, True, "555")
    run_all(s)
    t = s.tasks.store.get(tid)
    assert t.state is TaskState.COMPLETED, t.error_message
    assert not victim.exists()
    # the planner was asked twice in all: the approved step was replayed, not re-planned
    assert len(ad["ollama_local"].requests) == 2


def test_project_test_runs_the_projects_own_tests_without_ai(tmp_path: Path) -> None:
    repo = tmp_path / "demo"
    make_repo(repo)
    s, ad = setup(tmp_path, project=repo)
    tid = submit(s, "Git Demo test koro")
    run_all(s)
    t = s.tasks.store.get(tid)
    assert t.state is TaskState.COMPLETED, t.error_message
    assert "পাস" in t.result_summary and "OK" in t.result_summary
    assert ad["ollama_local"].requests == [] and ad["openai_codex"].requests == []
    done = [txt for _, txt in s.tasks.sent if "COMPLETE" in txt][0]
    assert "Git Demo Specialist" in done


def test_git_agent_shows_the_changes_read_only(tmp_path: Path) -> None:
    repo = tmp_path / "demo"
    make_repo(repo)
    (repo / "calc.py").write_text("def add(a, b):\n    return b + a\n", encoding="utf-8")
    s, ad = setup(tmp_path, project=repo)
    tid = submit(s, "git demo er git status dekhao")
    run_all(s)
    t = s.tasks.store.get(tid)
    assert t.state is TaskState.COMPLETED, t.error_message
    assert "calc.py" in t.result_summary and "main" in t.result_summary
    assert ad["ollama_local"].requests == []


def test_coding_specialist_role_and_project_context_reach_the_provider(tmp_path: Path) -> None:
    repo = tmp_path / "demo"
    make_repo(repo)

    def edit(request: object) -> str:
        (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n\n\n"
                                      "def sub(a, b):\n    return a - b\n", encoding="utf-8")
        return "Added sub()."
    s, ad = setup(tmp_path, codex=[edit], project=repo)
    tid = submit(s, "git demo te ekta api endpoint er jonno sub function add koro")
    run_all(s)
    t = s.tasks.store.get(tid)
    assert t.state is TaskState.COMPLETED, t.error_message
    req = ad["openai_codex"].requests[0]
    assert req.task_type == "coding"
    assert "backend architect" in req.system                     # role prompt (§59)
    assert "Git Demo Specialist" in req.system and "python -m unittest" in req.system
    assert "Click TV" not in req.system                           # only this project (§51)
