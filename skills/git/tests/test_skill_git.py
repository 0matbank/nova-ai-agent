"""git skill (Phase 15): registered projects only, fast-forward pull, commit on a
nova/ branch with a secret check, push only with approval — against real git and a
local bare remote (no network, no GitHub)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from core.config import load_config
from core.skills.api import PolicyDenied
from tests.mocks.skills import SkillEnv, make_skill_env

GIT = shutil.which("git") or "git"


def git(repo: Path, *args: str) -> str:
    return subprocess.run([GIT, "-C", str(repo), *args], check=True,  # noqa: S603
                          capture_output=True, text=True).stdout


def make_project(tmp: Path) -> tuple[Path, Path]:
    remote = tmp / "remote.git"
    subprocess.run([GIT, "init", "-q", "--bare", "-b", "main", str(remote)], check=True)  # noqa: S603
    repo = tmp / "work" / "demo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    for k, v in (("user.name", "t"), ("user.email", "t@t")):
        git(repo, "config", k, v)
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "-u", "origin", "main")
    return repo, remote


@pytest.fixture
def s(tmp_path: Path) -> tuple[SkillEnv, Path, Path]:
    repo, remote = make_project(tmp_path)
    env = make_skill_env(tmp_path)
    (tmp_path / "config" / "projects" / "demo.yaml").write_text(
        f"name: Demo\nlocal_folder: '{repo}'\ntest_command: \"python -m unittest\"\n",
        encoding="utf-8")
    env.runner.env.config = load_config(tmp_path / "config", tmp_path / "rt")
    return env, repo, remote


def test_status_and_diff_are_read_only(s: tuple[SkillEnv, Path, Path]) -> None:
    env, repo, _ = s
    (repo / "a.py").write_text("x = 2\n", encoding="utf-8")
    st = env.call("git", "status", {"project": "demo"})
    assert st.ok and st.data["branch"] == "main" and st.data["changed"] == ["a.py"]
    d = env.call("git", "diff", {"project": "demo"})
    assert d.ok and "+x = 2" in d.data["patch"] and env.tasks.buttons == []


def test_only_registered_projects_and_never_novas_own_folder(
        s: tuple[SkillEnv, Path, Path]) -> None:
    env, _, _ = s
    with pytest.raises(PolicyDenied, match="unknown project"):
        env.call("git", "status", {"project": "nova-ai-agent"})


def test_commit_goes_to_a_nova_branch_and_restores_the_checkout(
        s: tuple[SkillEnv, Path, Path]) -> None:
    env, repo, _ = s
    (repo / "a.py").write_text("x = 2\n", encoding="utf-8")
    r = env.call("git", "commit", {"project": "demo", "message": "Nova: x=2",
                                   "branch": "nova/task-7"})
    assert r.ok and env.tasks.buttons == []                     # no secrets → no approval
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert "nova/task-7" in git(repo, "branch")
    assert git(repo, "show", "nova/task-7:a.py") == "x = 2\n"
    with pytest.raises(PolicyDenied, match="nova/"):
        env.call("git", "commit", {"project": "demo", "message": "m", "branch": "main"})


def test_a_secret_in_the_change_needs_the_owners_approval(
        s: tuple[SkillEnv, Path, Path]) -> None:
    env, repo, _ = s
    (repo / "config.py").write_text('KEY = "AIza' + "c" * 35 + '"\n', encoding="utf-8")
    r, _ = env.call_with_approval("git", "commit", {"project": "demo", "message": "m",
                                                    "branch": "nova/task-8"}, approve=False)
    assert r is None and "nova/task-8" not in git(repo, "branch")


def test_push_always_needs_approval_never_forces_and_is_verified(
        s: tuple[SkillEnv, Path, Path]) -> None:
    env, repo, remote = s
    (repo / "a.py").write_text("x = 3\n", encoding="utf-8")
    env.call("git", "commit", {"project": "demo", "message": "m", "branch": "nova/task-9"})
    r, _ = env.call_with_approval("git", "push", {"project": "demo", "branch": "nova/task-9"},
                                  approve=False)
    assert r is None and "nova/task-9" not in git(remote, "branch")      # rejected: not pushed
    r, _ = env.call_with_approval("git", "push", {"project": "demo", "branch": "nova/task-9"})
    assert r is not None and r.ok and r.evidence["pushed"]
    assert git(remote, "rev-parse", "nova/task-9") == git(repo, "rev-parse", "nova/task-9")
    assert "main" in git(remote, "branch")                   # main untouched
    with pytest.raises(PolicyDenied, match="nova/"):
        env.call("git", "push", {"project": "demo", "branch": "main"})


def test_pull_is_fast_forward_only_and_never_over_local_work(
        s: tuple[SkillEnv, Path, Path], tmp_path: Path) -> None:
    env, repo, remote = s
    other = tmp_path / "other"
    subprocess.run([GIT, "clone", "-q", str(remote), str(other)], check=True)  # noqa: S603
    for k, v in (("user.name", "o"), ("user.email", "o@o")):
        git(other, "config", k, v)
    (other / "b.py").write_text("y = 1\n", encoding="utf-8")
    git(other, "add", "-A")
    git(other, "commit", "-qm", "upstream")
    git(other, "push", "-q", "origin", "main")
    r = env.call("git", "pull", {"project": "demo"})
    assert r.ok and r.data["pulled"] and (repo / "b.py").exists()
    (repo / "a.py").write_text("local edit\n", encoding="utf-8")
    with pytest.raises(PolicyDenied, match="uncommitted"):
        env.call("git", "pull", {"project": "demo"})
