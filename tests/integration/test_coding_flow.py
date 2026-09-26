"""Phase 12: coding request → coding provider edits a registered project →
Nova's own test run decides. Real git repo + real unittest run; the coding
provider is a scripted stand-in (the live check is scripts/coding_drill.py)."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from core.config import load_config
from core.orchestrator.executor import UserRequestExecutor
from core.queue.states import TaskState
from core.router.intent import IntentRouter
from models.router import ProviderRouter
from providers.provider_base import (
    HealthState,
    ProviderRequest,
    ProviderResult,
    ResultStatus,
)
from tests.mocks.providers import FakeAdapter
from tests.mocks.skills import SkillEnv, make_skill_env

BUGGY = "def average(v):\n    return sum(v) / len(v)\n"
FIXED = "def average(v):\n    return sum(v) / len(v) if v else 0.0\n"
TESTS = ("import unittest\nfrom stats import average\n\n\nclass T(unittest.TestCase):\n"
         "    def test_empty(self):\n        self.assertEqual(average([]), 0.0)\n\n"
         "    def test_mean(self):\n        self.assertEqual(average([2, 4]), 3)\n")
REQUEST = "demo project-er test fail korche, bug fix koro"


GIT = shutil.which("git") or "git"


def git(repo: Path, *args: str) -> str:
    return subprocess.run([GIT, "-C", str(repo), *args], check=True,  # noqa: S603
                          capture_output=True, text=True).stdout


def make_repo(root: Path) -> Path:
    repo = root / "demo"
    repo.mkdir()
    (repo / "stats.py").write_text(BUGGY, encoding="utf-8")
    (repo / "test_stats.py").write_text(TESTS, encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "t")
    git(repo, "config", "user.email", "t@t")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "buggy")
    return repo


class Coder(FakeAdapter):
    """Scripted coding provider: each call runs the next edit on the workspace."""

    def __init__(self, edits: list[Callable[[Path], None]]) -> None:
        super().__init__("openai_codex", capabilities=frozenset({"coding"}))
        self.edits = list(edits)
        self._set(HealthState.HEALTHY, "test")

    async def _complete(self, request: ProviderRequest) -> ProviderResult:
        self.requests.append(request)
        assert request.workspace and "code.edit" in request.allowed_tools
        if self.edits:
            self.edits.pop(0)(Path(request.workspace))
        return ProviderResult(ResultStatus.OK, self.name, model="fake",
                              answer="average() এখন খালি list-এ 0.0 দেয়।",
                              session_id="thread-9")


def fix(repo: Path) -> None:
    (repo / "stats.py").write_text(FIXED, encoding="utf-8")


def half_fix(repo: Path) -> None:
    (repo / "stats.py").write_text("def average(v):\n    return 0.0\n", encoding="utf-8")


def setup(tmp_path: Path, coder: Coder, folder: str | None = None) -> tuple[SkillEnv, Path]:
    repo = make_repo(tmp_path)
    s = make_skill_env(tmp_path)
    (tmp_path / "config" / "projects" / "demo.yaml").write_text(
        f"name: Demo\nlocal_folder: '{folder or repo}'\n"
        'test_command: "python -m unittest -v"\n', encoding="utf-8")
    cfg = load_config(tmp_path / "config", tmp_path / "rt")
    s.runner.env.config = cfg
    adapters: dict[str, Any] = {n: FakeAdapter(n, state=HealthState.UNAVAILABLE)
                                for n in cfg.providers.providers}
    adapters["openai_codex"] = coder
    router = ProviderRouter(cfg.providers, adapters)
    s.tasks.engine.register("user_request",
                            UserRequestExecutor(IntentRouter(router), router, s.runner))
    return s, repo


def run(s: SkillEnv, text: str = REQUEST) -> Any:
    tid = int(s.tasks.store.create(title=text, request_text=text, task_type="user_request",
                                   channel="telegram", chat_id="555").id)
    for _ in range(5):
        if not asyncio.run(s.tasks.engine.run_once()):
            break
    return s.tasks.store.get(tid)


def test_clean_repo_fix_is_verified_by_the_projects_own_tests(tmp_path: Path) -> None:
    coder = Coder([fix])
    s, repo = setup(tmp_path, coder)
    t = run(s)
    assert t.state is TaskState.COMPLETED, t.error_message
    assert "Test পাস ✅" in t.result_summary and "• stats.py" in t.result_summary
    assert "average() এখন খালি list-এ 0.0 দেয়।" in t.result_summary
    assert s.tasks.buttons == []                                 # clean tree → no approval
    assert len(git(repo, "log", "--oneline").splitlines()) == 1   # never committed
    assert "__pycache__" not in t.result_summary
    (req,) = coder.requests
    assert "do not commit, push" in req.system and "Bangla" in req.system


def test_dirty_repo_needs_approval_first(tmp_path: Path) -> None:
    coder = Coder([fix])
    s, repo = setup(tmp_path, coder)
    (repo / "notes.txt").write_text("owner's unsaved work", encoding="utf-8")
    t = run(s)
    assert t.state is TaskState.WAITING_APPROVAL and len(s.tasks.buttons) == 1
    assert coder.requests == []                                    # nothing edited yet
    nonce = s.tasks.last_callback(True).split(":")[1]
    s.tasks.approvals.decide(nonce, True, "555")          # task resumes by itself
    t = run_existing(s, t.id)
    assert t.state is TaskState.COMPLETED and len(coder.requests) == 1
    assert (repo / "notes.txt").read_text(encoding="utf-8") == "owner's unsaved work"


def run_existing(s: SkillEnv, tid: int) -> Any:
    for _ in range(5):
        if not asyncio.run(s.tasks.engine.run_once()):
            break
    return s.tasks.store.get(tid)


def test_still_failing_after_follow_up_goes_to_the_owner(tmp_path: Path) -> None:
    coder = Coder([half_fix, half_fix])
    s, _ = setup(tmp_path, coder)
    t = run(s)
    assert t.state is TaskState.FAILED and t.error_code == "BLOCKED_NEEDS_USER"
    assert "Test ফেল ❌" in t.error_message and "বাতিল করতে" in t.error_message
    assert len(coder.requests) == 2 and t.retry_count == 0
    assert "tests still fail" in (coder.requests[1].previous_attempt or "")
    assert "test_mean" in (coder.requests[1].previous_attempt or "")    # real test output


def test_unknown_project_asks_which(tmp_path: Path) -> None:
    coder = Coder([fix])
    s, _ = setup(tmp_path, coder)
    t = run(s, "amar code e bug ase fix koro")
    assert t.state is TaskState.COMPLETED and "কোন project" in t.result_summary
    assert "Demo" in t.result_summary and coder.requests == []


def test_novas_own_folders_are_never_edited(tmp_path: Path) -> None:
    coder = Coder([fix])
    app_dir = Path(__file__).resolve().parents[2]
    s, _ = setup(tmp_path, coder, folder=str(app_dir))
    t = run(s)
    assert t.state is TaskState.FAILED and t.error_code == "PERMISSION_DENIED"
    assert coder.requests == []


@pytest.mark.parametrize(("text", "found"), [(REQUEST, "demo"), ("Demo te bug fix koro", "demo"),
                                             ("demolition bug", None)])
def test_find_project(tmp_path: Path, text: str, found: str | None) -> None:
    from core.orchestrator.coding import find_project
    s, _ = setup(tmp_path, Coder([]))
    got = find_project(s.runner.env.config, text)
    assert (got[0] if got else None) == found


def fix_and_commit(repo: Path) -> None:
    fix(repo)
    git(repo, "commit", "-qam", "agent committed on its own")


def test_a_commit_by_the_coding_agent_is_caught(tmp_path: Path) -> None:
    coder = Coder([fix_and_commit])
    s, repo = setup(tmp_path, coder)
    first = git(repo, "rev-parse", "HEAD").strip()
    t = run(s)
    assert t.state is TaskState.FAILED and t.error_code == "BLOCKED_NEEDS_USER"
    assert "commit" in t.error_message and f"reset --soft {first[:12]}" in t.error_message
