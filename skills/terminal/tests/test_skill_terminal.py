from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from core.skills.api import PolicyDenied
from tests.mocks.skills import SkillEnv, make_skill_env


@pytest.fixture
def s(tmp_path: Path) -> SkillEnv:
    return make_skill_env(tmp_path)


def ro(program: str, args: list[str]) -> bool:
    return bool(sys.modules["nova_skill_terminal_run"].is_read_only(program, args))


@pytest.mark.parametrize("program,args,expected", [
    ("git", ["--version"], True), ("python", ["--version"], True),
    ("git", ["status"], True), ("git", ["log", "--oneline", "-5"], True),
    ("git", ["diff", "--output=x.txt"], False), ("git", ["push"], False),
    ("git", ["-c", "core.pager=evil", "status"], False), ("git", ["branch", "-D", "x"], False),
    ("whoami", [], True), ("whoami", ["/all"], False), ("ipconfig", ["/all"], True),
    ("ipconfig", ["/release"], False), ("python", ["-c", "print(1)"], False),
    ("unknown-tool", ["--version"], False), ("where", ["git"], True),
])
def test_allowlist(s: SkillEnv, program: str, args: list[str], expected: bool) -> None:
    assert ro(program, args) is expected


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_read_only_runs(s: SkillEnv) -> None:
    r = s.call("terminal", "run", {"program": "git", "args": ["--version"]})
    assert r.ok and "git version" in r.data["stdout"] and s.tasks.buttons == []


def test_arbitrary_program_needs_approval(s: SkillEnv) -> None:
    out = s.workspace / "py.txt"
    params = {"program": sys.executable, "args": ["-c", f"open(r'{out}','w').write('1')"]}
    s.call_with_approval("terminal", "run", params, approve=False)
    assert not out.exists()
    r, _ = s.call_with_approval("terminal", "run", params)
    assert r is not None and r.ok and out.exists()


@pytest.mark.parametrize("program", ["cmd", "powershell", "bash"])
def test_shells_refused(s: SkillEnv, program: str) -> None:
    if shutil.which(program) is None:
        pytest.skip(f"{program} not present")
    with pytest.raises(PolicyDenied):
        s.call("terminal", "run", {"program": program, "args": ["/c", "echo hi"]})


def test_missing_program_refused(s: SkillEnv) -> None:
    with pytest.raises(PolicyDenied, match="not found"):
        s.call("terminal", "run", {"program": "definitely-not-a-real-program-xyz"})
