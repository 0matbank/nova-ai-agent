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
    ErrorCategory,
    HealthState,
    ProviderError,
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
    # every coding agent starts from the real file layout (agy has no list tool)
    assert "stats.py" in req.context and "test_stats.py" in req.context


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


class Crasher(FakeAdapter):
    """Makes part of the change, then dies with a server error (every retry too)."""

    def __init__(self) -> None:
        super().__init__("openai_codex", capabilities=frozenset({"coding"}))
        self._set(HealthState.HEALTHY, "test")

    async def _complete(self, request: ProviderRequest) -> ProviderResult:
        self.requests.append(request)
        path = Path(request.workspace or ".") / "stats.py"
        if not path.read_text(encoding="utf-8").startswith("# started by codex"):
            path.write_text("# started by codex\n" + BUGGY, encoding="utf-8")
        raise ProviderError(ErrorCategory.SERVER_ERROR, "codex: 503 overloaded mid-task")


def finish(repo: Path) -> None:
    """The second provider builds on what is already there (keeps codex's line)."""
    path = repo / "stats.py"
    path.write_text(path.read_text(encoding="utf-8").replace(
        "return sum(v) / len(v)\n", "return sum(v) / len(v) if v else 0.0\n"), encoding="utf-8")


def test_provider_dies_mid_task_and_the_next_one_continues(tmp_path: Path) -> None:
    """Plan Phase 14 pass: provider A fails → provider B continues from the
    current state (failure summary + the diff already made), not from zero."""
    crasher = Crasher()
    google = Coder([finish])
    google.name = "google_antigravity"
    s, repo = setup(tmp_path, google)
    router = s.tasks.engine.executors["user_request"].providers  # type: ignore[attr-defined]
    router.adapters["openai_codex"] = crasher
    router.adapters["google_antigravity"] = google
    t = run(s)
    assert t.state is TaskState.COMPLETED, t.error_message
    body = git(repo, "show", f"nova/task-{t.id}:stats.py")              # Phase 15: on its branch
    assert body.startswith("# started by codex") and "if v else 0.0" in body   # both edits
    (handed,) = google.requests
    prev = handed.previous_attempt or ""
    assert "openai_codex" in prev and "SERVER_ERROR" in prev and "do not start over" in prev
    assert "+# started by codex" in prev                      # the diff so far was handed over
    assert "🔁 openai_codex" in t.result_summary and "Test পাস ✅" in t.result_summary
    assert len(git(repo, "log", "--oneline").splitlines()) == 1


def test_all_providers_down_then_retry_continues_from_the_saved_state(tmp_path: Path) -> None:
    """Plan §9.2 (audit 2026-10-15): the failover state is in the task's checkpoint,
    so the retry continues from it — no fresh start, no approval for its own edits."""
    crasher = Crasher()
    google = Coder([finish])
    google.name = "google_antigravity"
    google._set(HealthState.UNAVAILABLE, "down for now")
    google.check_health = (lambda: _health(google))  # type: ignore[method-assign]
    s, repo = setup(tmp_path, google)
    router = s.tasks.engine.executors["user_request"].providers  # type: ignore[attr-defined]
    router.adapters["openai_codex"] = crasher
    router.adapters["google_antigravity"] = google
    tid = int(s.tasks.store.create(title=REQUEST, request_text=REQUEST,
                                   task_type="user_request", channel="telegram",
                                   chat_id="555").id)
    asyncio.run(s.tasks.engine.run_once())
    t = s.tasks.store.get(tid)
    assert t.state is TaskState.RETRYING and google.requests == []
    assert (repo / "stats.py").read_text(encoding="utf-8").startswith("# started by codex")
    google._set(HealthState.HEALTHY, "back")                    # Google is back
    t = run_existing(s, tid)
    assert t.state is TaskState.COMPLETED, t.error_message
    assert s.tasks.buttons == []                                # own edits: no approval asked
    prev = google.requests[0].previous_attempt or ""
    assert "earlier attempt" in prev and "openai_codex" in prev and "+# started by codex" in prev
    body = git(repo, "show", f"nova/task-{tid}:stats.py")               # Phase 15: on its branch
    assert body.startswith("# started by codex") and "if v else 0.0" in body


async def _health(adapter: Any) -> Any:
    return adapter.health


class Slow(FakeAdapter):
    """A coding agent that keeps working until it is cancelled."""

    def __init__(self) -> None:
        super().__init__("openai_codex", capabilities=frozenset({"coding"}))
        self._set(HealthState.HEALTHY, "test")
        self.cancelled = False

    async def _complete(self, request: ProviderRequest) -> ProviderResult:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        raise AssertionError("should have been cancelled")


def test_cancel_stops_a_running_coding_agent_within_seconds(tmp_path: Path) -> None:
    """Audit 2026-10-15: /cancel used to wait for the whole step (up to ~30 min)."""
    slow = Slow()
    s, _ = setup(tmp_path, slow)
    tid = int(s.tasks.store.create(title=REQUEST, request_text=REQUEST,
                                   task_type="user_request", channel="telegram",
                                   chat_id="555").id)

    async def go() -> None:
        job = asyncio.create_task(s.tasks.engine.run_once())
        for _ in range(100):
            if any("কোড বদলানোর কাজ চলছে" in txt for _, txt in s.tasks.sent):
                break
            await asyncio.sleep(0.05)
        s.tasks.store.request_cancel(tid)
        await asyncio.wait_for(job, timeout=10)
    asyncio.run(go())
    t = s.tasks.store.get(tid)
    assert t.state is TaskState.CANCELLED and slow.cancelled
    assert any("থামাতে /cancel" in txt for _, txt in s.tasks.sent)       # progress shown


# ------------------------------------------- Phase 15: edit → test → diff → approval → push

def add_remote(repo: Path, tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    subprocess.run([GIT, "init", "-q", "--bare", str(remote)], check=True)  # noqa: S603
    git(repo, "remote", "add", "origin", str(remote))
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    git(repo, "push", "-q", "-u", "origin", branch)
    return remote


def test_phase15_edit_test_diff_approval_push(tmp_path: Path) -> None:
    """Plan Phase 15 pass: repo edit → test → diff → approval → push."""
    coder = Coder([fix])
    s, repo = setup(tmp_path, coder)
    remote = add_remote(repo, tmp_path)
    main = git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    t = run(s)
    assert t.state is TaskState.WAITING_APPROVAL and len(s.tasks.buttons) == 1   # push = RED
    report = next(txt for _, txt in s.tasks.sent if "nova/task-" in txt and "Approve" in txt)
    assert "Test পাস ✅" in report and "• stats.py" in report and "1 file changed" in report
    assert f"nova/task-{t.id}" not in git(remote, "branch")                      # not pushed yet
    nonce = s.tasks.last_callback(True).split(":")[1]
    s.tasks.approvals.decide(nonce, True, "555")
    t = run_existing(s, t.id)
    assert t.state is TaskState.COMPLETED, t.error_message
    assert "🚀" in t.result_summary and f"nova/task-{t.id}" in t.result_summary
    assert git(remote, "show", f"nova/task-{t.id}:stats.py") == FIXED              # pushed
    assert git(remote, "show", f"{main}:stats.py") == BUGGY                        # main untouched
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == main          # checkout kept
    assert len(coder.requests) == 1                     # the AI ran once, not again on resume


def test_phase15_rejected_push_sends_nothing(tmp_path: Path) -> None:
    coder = Coder([fix])
    s, repo = setup(tmp_path, coder)
    remote = add_remote(repo, tmp_path)
    t = run(s)
    nonce = s.tasks.last_callback(False).split(":")[1]
    s.tasks.approvals.decide(nonce, False, "555")
    t = run_existing(s, t.id)
    assert t.state is not TaskState.COMPLETED
    assert f"nova/task-{t.id}" not in git(remote, "branch")
    assert f"nova/task-{t.id}" in git(repo, "branch")         # the commit stays on this PC
