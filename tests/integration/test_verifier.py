"""Phase 18 pass — "Multi-step কাজ independent verificationসহ complete হয়": the
verifier looks at the PC / git / tests itself (plan §54), NEEDS WORK runs the action
again and recovers (§30, §53), and high-impact code gets a read-only review from a
DIFFERENT provider before it is committed (§8.10)."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path

from core.config import load_config
from core.orchestrator.executor import UserRequestExecutor
from core.queue.engine import Verdict, VerdictStatus
from core.queue.states import TaskState
from core.router.intent import IntentRouter
from core.verifier import check_effect, pids_alive
from models.router import ProviderRouter
from providers.provider_base import HealthState
from tests.mocks.providers import FakeAdapter
from tests.mocks.skills import SkillEnv, make_skill_env

GIT = shutil.which("git") or "git"


def setup(tmp_path: Path, local: list | None = None, codex: list | None = None,
          agy: list | None = None, project: Path | None = None
          ) -> tuple[SkillEnv, dict[str, FakeAdapter]]:
    s = make_skill_env(tmp_path)
    if project is not None:
        (tmp_path / "config" / "projects" / "git-demo.yaml").write_text(
            f"name: Git Demo\nlocal_folder: '{project}'\n"
            "test_command: \"python -m unittest -v\"\naliases: [gitdemo]\n", encoding="utf-8")
        s.runner.env.config = load_config(tmp_path / "config", tmp_path / "rt")
    cfg = load_config()
    ad = {n: FakeAdapter(n, state=HealthState.UNAVAILABLE) for n in cfg.providers.providers}
    ad["ollama_local"] = FakeAdapter("ollama_local", local or [])
    ad["openai_codex"] = FakeAdapter("openai_codex", codex or [])
    if agy is not None:
        ad["google_antigravity"] = FakeAdapter("google_antigravity", agy)
    router = ProviderRouter(cfg.providers, ad)
    s.tasks.engine.register("user_request",
                            UserRequestExecutor(IntentRouter(router), router, s.runner))
    return s, ad


def submit(s: SkillEnv, text: str, rounds: int = 6) -> int:
    tid = int(s.tasks.store.create(title=text, request_text=text, task_type="user_request",
                                   channel="telegram", chat_id="555").id)
    for _ in range(rounds):
        if not asyncio.run(s.tasks.engine.run_once()):
            break
    return tid


def call(skill: str, tool: str, **params: object) -> str:
    return json.dumps({"action": "call", "skill": skill, "tool": tool, "params": params})


def answer(text: str) -> str:
    return json.dumps({"action": "answer", "answer": text}, ensure_ascii=False)


# ------------------------------------------------------------- verdict model

def test_verdict_statuses_and_text() -> None:
    assert Verdict(True, "x").final is VerdictStatus.PASS
    assert Verdict(False, "x").final is VerdictStatus.NEEDS_WORK
    v = Verdict(True, "no tests", VerdictStatus.LIMITED, ("a ✓", "b ✓"))
    assert v.text() == "☑️ PASS WITH KNOWN LIMITATIONS — no tests · a ✓ · b ✓"


def test_process_checks_look_at_the_pc() -> None:
    me = os.getpid()
    assert pids_alive([me, 999999]) == [me]
    ok, proof = check_effect({"skill": "app-control", "tool": "open", "params": {"app": "x"},
                              "evidence": {"running_pids": [999999]}}, Path)
    assert not ok and "চলছে না" in proof
    ok, _ = check_effect({"skill": "app-control", "tool": "close", "params": {"app": "x"},
                          "evidence": {"still_running": [me]}}, Path)
    assert not ok


# ------------------------------------------------------- operator: claimed ≠ real

def test_a_claimed_file_that_is_not_there_is_redone_and_recovers(tmp_path: Path) -> None:
    target = tmp_path / "rt" / "workspace" / "notes.txt"

    def lie(_: object) -> str:                  # says done, but the file is gone again
        target.unlink(missing_ok=True)
        return answer("notes.txt লিখে দিয়েছি।")
    s, ad = setup(tmp_path, local=[
        call("files", "write", path=str(target), content="hello"), lie,      # attempt 1
        call("files", "write", path=str(target), content="hello"),           # attempt 2
        answer("notes.txt লিখে দিয়েছি।"),
    ])
    tid = submit(s, f"{target} file e hello likhe dao")
    t = s.tasks.store.get(tid)
    assert t.state is TaskState.COMPLETED and target.read_text(encoding="utf-8") == "hello"
    assert t.retry_count == 1
    msgs = [m for _, m in s.tasks.sent]
    assert any("যাচাইয়ে ঘাটতি" in m and "notes.txt নেই ✗" in m for m in msgs)
    done = next(m for m in msgs if "COMPLETE" in m)
    assert "✅ PASS" in done and "notes.txt আছে ✓" in done and "AUTO_RECOVERED" in done


def test_retries_exhausted_end_with_a_blocker_report(tmp_path: Path) -> None:
    target = tmp_path / "rt" / "workspace" / "x.txt"

    def lie(_: object) -> str:
        target.unlink(missing_ok=True)
        return answer("লিখেছি।")
    script: list = []
    for _ in range(3):
        script += [call("files", "write", path=str(target), content="1"), lie]
    s, _ = setup(tmp_path, local=script)
    tid = submit(s, f"{target} file e 1 likhe dao", rounds=10)
    t = s.tasks.store.get(tid)
    assert t.state is TaskState.FAILED and t.error_code == "VERIFICATION_FAILED"
    report = next(m for _, m in s.tasks.sent if "FAILED (VERIFICATION_FAILED)" in m)
    assert "3 বার চেষ্টা করেছি (FAILED_SAFE)" in report and "কী লাগবে" in report


# ------------------------------------------------------- cross-provider review

def make_repo(folder: Path) -> None:
    folder.mkdir(parents=True)
    subprocess.run([GIT, "init", "-q", "-b", "main", str(folder)], check=True)  # noqa: S603
    for k, v in (("user.name", "t"), ("user.email", "t@t")):
        subprocess.run([GIT, "-C", str(folder), "config", k, v], check=True)  # noqa: S603
    (folder / "auth.py").write_text("def check(pw):\n    return pw == 'admin'\n",
                                    encoding="utf-8")
    (folder / "test_auth.py").write_text(
        "import unittest\nfrom auth import check\n\n\nclass T(unittest.TestCase):\n"
        "    def test_wrong(self):\n        self.assertFalse(check('x'))\n", encoding="utf-8")
    subprocess.run([GIT, "-C", str(folder), "add", "-A"], check=True)  # noqa: S603
    subprocess.run([GIT, "-C", str(folder), "commit", "-qm", "base"], check=True)  # noqa: S603


def review(verdict: str, *issues: tuple[str, str]) -> str:
    return json.dumps({"verdict": verdict, "summary": f"{verdict} summary",
                       "issues": [{"severity": sev, "file": "auth.py", "problem": p}
                                  for sev, p in issues]})


def test_security_change_is_reviewed_by_another_provider_fixed_and_verified(
        tmp_path: Path) -> None:
    repo = tmp_path / "demo"
    make_repo(repo)

    def first(_: object) -> str:
        (repo / "auth.py").write_text("import os\n\ndef check(pw):\n    return pw == "
                                      "os.environ.get('PW', 'admin')\n", encoding="utf-8")
        return "Read the password from the environment."

    def second(_: object) -> str:
        (repo / "auth.py").write_text("import hmac\nimport os\n\ndef check(pw):\n    want = "
                                      "os.environ.get('PW')\n    return bool(want) and "
                                      "hmac.compare_digest(pw, want)\n", encoding="utf-8")
        return "No default password; constant-time compare."
    s, ad = setup(tmp_path, codex=[first, second], project=repo, agy=[
        review("changes_needed", ("high", "falls back to the default password 'admin'")),
        review("approve")])
    tid = submit(s, "git demo te auth.py er security fix koro: hardcoded password sorao")
    t = s.tasks.store.get(tid)
    assert t.state is TaskState.COMPLETED, t.error_message
    reviews = ad["google_antigravity"].requests
    assert [r.task_type for r in reviews] == ["review", "review"]
    assert all(r.workspace is None and not r.allowed_tools for r in reviews)  # read-only
    assert "'admin'" in reviews[0].context                                  # saw the diff
    assert [r.task_type for r in ad["openai_codex"].requests] == ["coding", "coding"]
    assert "falls back to the default password" in ad["openai_codex"].requests[1].previous_attempt
    done = next(m for _, m in s.tasks.sent if "COMPLETE" in m)
    assert "✅ PASS" in done and "আলাদা reviewer google_antigravity: approve ✓" in done
    assert "Nova-র নিজের test run: pass ✓" in done and "-এ আছে ✓" in done      # git checked
    branch = f"nova/task-{tid}"
    out = subprocess.run([GIT, "-C", str(repo), "show", f"{branch}:auth.py"],  # noqa: S603
                         capture_output=True, text=True, check=True).stdout
    assert "compare_digest" in out


def test_a_remaining_must_fix_objection_stops_the_commit(tmp_path: Path) -> None:
    repo = tmp_path / "demo"
    make_repo(repo)

    def edit(_: object) -> str:
        (repo / "auth.py").write_text("def check(pw):\n    return pw == 'root'\n",
                                      encoding="utf-8")
        return "changed"
    bad = review("changes_needed", ("high", "still a hardcoded password"))
    s, ad = setup(tmp_path, codex=[edit, edit], project=repo, agy=[bad, bad])
    tid = submit(s, "git demo te auth er security fix koro")
    t = s.tasks.store.get(tid)
    assert t.state is TaskState.FAILED and t.error_code == "BLOCKED_NEEDS_USER"
    assert "commit/push করিনি" in (t.error_message or "")
    branches = subprocess.run([GIT, "-C", str(repo), "branch"], capture_output=True,  # noqa: S603
                              text=True).stdout
    assert "nova/" not in branches                                          # nothing committed


def test_no_other_provider_means_pass_with_known_limitations(tmp_path: Path) -> None:
    repo = tmp_path / "demo"
    make_repo(repo)

    def edit(_: object) -> str:
        (repo / "auth.py").write_text("def check(pw):\n    return False\n", encoding="utf-8")
        return "changed"
    # Antigravity etc. unavailable; the only other one (local) answers without a verdict —
    # that is never taken as an approval
    s, ad = setup(tmp_path, codex=[edit], project=repo, local=["looks fine"])
    tid = submit(s, "git demo te auth er security fix koro")
    t = s.tasks.store.get(tid)
    assert t.state is TaskState.COMPLETED, t.error_message
    done = next(m for _, m in s.tasks.sent if "COMPLETE" in m)
    assert "PASS WITH KNOWN LIMITATIONS" in done and "independent review হয়নি" in done
    assert [r.task_type for r in ad["openai_codex"].requests] == ["coding"]  # never self-review


def test_small_ordinary_change_is_not_reviewed(tmp_path: Path) -> None:
    repo = tmp_path / "demo"
    make_repo(repo)

    def edit(_: object) -> str:            # an ordinary file (auth.py would be sensitive)
        (repo / "util.py").write_text("def double(x):\n    return 2 * x\n", encoding="utf-8")
        return "added util.double"
    s, ad = setup(tmp_path, codex=[edit], project=repo, agy=[])
    tid = submit(s, "git demo te util.py te ekta double function add koro")
    assert s.tasks.store.get(tid).state is TaskState.COMPLETED
    assert ad["google_antigravity"].requests == []                 # quota kept (§58 rule 11)
