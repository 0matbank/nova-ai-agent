"""Phase 12 live drill: a coding request → Codex CLI (signed in with ChatGPT)
edits a project → Nova runs the project's tests → report. Uses a fresh copy of
a small buggy demo repo in a temp folder, so no real project is touched.

    uv run python scripts/coding_drill.py

PASS = task COMPLETED, tests pass after Codex's change, nothing committed,
no approval needed (clean git tree → code.edit safety check passes).
"""

import asyncio
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import load_config, load_secrets  # noqa: E402
from core.orchestrator.executor import UserRequestExecutor  # noqa: E402
from core.router.intent import IntentRouter  # noqa: E402
from models.router import ProviderRouter  # noqa: E402
from providers.registry import ProviderRegistry  # noqa: E402
from tests.mocks.skills import make_skill_env  # noqa: E402

STATS = '''"""Tiny statistics helpers (Nova Codex demo project)."""


def average(values):
    """Arithmetic mean of a list of numbers; 0.0 for an empty list."""
    return sum(values) / len(values)


def median(values):
    """Middle value of the sorted list (mean of the two middle ones if even)."""
    s = sorted(values)
    mid = len(s) // 2
    return s[mid]
'''
TESTS = '''import unittest

from stats import average, median


class StatsTest(unittest.TestCase):
    def test_average(self):
        self.assertEqual(average([2, 4, 6]), 4)

    def test_average_empty(self):
        self.assertEqual(average([]), 0.0)

    def test_median_odd(self):
        self.assertEqual(median([3, 1, 2]), 2)

    def test_median_even(self):
        self.assertEqual(median([4, 1, 3, 2]), 2.5)


if __name__ == "__main__":
    unittest.main()
'''
GIT = shutil.which("git") or "git"
REQUEST = "drill-demo project-er test gulo fail korche, bug fix koro"


def make_repo(folder: Path) -> None:
    folder.mkdir(parents=True)
    (folder / "stats.py").write_text(STATS, encoding="utf-8")
    (folder / "test_stats.py").write_text(TESTS, encoding="utf-8")
    for args in (["init", "-q"], ["config", "user.name", "Nova AI"],
                 ["config", "user.email", "nova@localhost"], ["add", "-A"],
                 ["commit", "-q", "-m", "buggy demo"]):
        subprocess.run([GIT, "-C", str(folder), *args], check=True)  # noqa: S603


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="nova-coding-drill-"))
    # Codex's Windows sandbox cannot write under %TEMP%; use the workspace folder.
    repo = load_config().path("workspace_dir") / f".drill-demo-{tmp.name[-8:]}"
    make_repo(repo)
    try:
        return await drill(tmp, repo)
    finally:
        shutil.rmtree(repo, ignore_errors=True)


async def drill(tmp: Path, repo: Path) -> int:
    s = make_skill_env(tmp)
    (tmp / "config" / "projects" / "drill-demo.yaml").write_text(
        f"name: Drill Demo\nlocal_folder: '{repo}'\ntest_command: \"python -m unittest -v\"\n",
        encoding="utf-8")
    cfg = load_config(tmp / "config", tmp / "rt")
    s.runner.env.config = cfg
    router = ProviderRouter(cfg.providers, ProviderRegistry.from_config(
        cfg, load_secrets(load_config().path("secrets_dir"))).adapters)
    s.tasks.engine.register("user_request",
                            UserRequestExecutor(IntentRouter(router), router, s.runner))
    t0 = time.time()
    tid = int(s.tasks.store.create(title=REQUEST, request_text=REQUEST, task_type="user_request",
                                   channel="telegram", chat_id="555").id)
    for _ in range(4):
        if not await s.tasks.engine.run_once():
            break
    t = s.tasks.store.get(tid)
    print(f"{t.state.value} in {time.time() - t0:.0f}s")
    print((t.result_summary or f"{t.error_code}: {t.error_message}")[:2500])
    tests = subprocess.run([sys.executable, "-m", "unittest"], cwd=repo,  # noqa: S603
                           capture_output=True, text=True)
    log = subprocess.run([GIT, "-C", str(repo), "log", "--oneline"],  # noqa: S603
                         capture_output=True, text=True).stdout.strip().splitlines()
    print(f"independent test run: {'PASS' if tests.returncode == 0 else 'FAIL'}; "
          f"commits: {len(log)} (must stay 1); approvals asked: {len(s.tasks.buttons)}")
    ok = (t.state.value == "COMPLETED" and tests.returncode == 0 and len(log) == 1
          and not s.tasks.buttons)
    print(f"PHASE 12 CODING DRILL: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
