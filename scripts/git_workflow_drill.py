"""Phase 15 live drill — full coding workflow with the real coding provider:

  fresh buggy repo + a local bare 'origin' (no GitHub) → git pull → real Codex
  edits → Nova runs the tests → commit on nova/task-<id> → report → push
  approval → (the drill approves, standing in for the owner) → push → verify
  the remote branch, main untouched.

    uv run python scripts/git_workflow_drill.py

PASS = task COMPLETED; origin has nova/task-<id> with passing tests; origin's
main still has the buggy code; the AI ran once (not again after the approval).
"""

import asyncio
import importlib.util
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

_spec = importlib.util.spec_from_file_location(
    "coding_drill", Path(__file__).with_name("coding_drill.py"))
assert _spec and _spec.loader
drill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(drill)
GIT = shutil.which("git") or "git"


def git(repo: Path, *args: str) -> str:
    return subprocess.run([GIT, "-C", str(repo), *args], capture_output=True,  # noqa: S603
                          text=True).stdout


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="nova-git-drill-"))
    base = load_config().path("workspace_dir") / f".git-drill-{tmp.name[-8:]}"
    repo, remote = base / "demo", base / "remote.git"
    drill.make_repo(repo)
    subprocess.run([GIT, "init", "-q", "--bare", str(remote)], check=True)  # noqa: S603
    git(repo, "remote", "add", "origin", str(remote))
    main_branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    git(repo, "push", "-q", "-u", "origin", main_branch)
    try:
        return await run(tmp, repo, remote, main_branch)
    finally:
        shutil.rmtree(base, ignore_errors=True)


async def run(tmp: Path, repo: Path, remote: Path, main_branch: str) -> int:
    s = make_skill_env(tmp)
    (tmp / "config" / "projects" / "drill-demo.yaml").write_text(
        f"name: Drill Demo\nlocal_folder: '{repo}'\ntest_command: \"python -m unittest -v\"\n",
        encoding="utf-8")
    cfg = load_config(tmp / "config", tmp / "rt")
    s.runner.env.config = cfg
    adapters = ProviderRegistry.from_config(
        cfg, load_secrets(load_config().path("secrets_dir"))).adapters
    calls = {"n": 0}
    real = adapters["openai_codex"].complete

    async def counted(request):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return await real(request)
    adapters["openai_codex"].complete = counted  # type: ignore[method-assign]
    router = ProviderRouter(cfg.providers, adapters)
    s.tasks.engine.register("user_request",
                            UserRequestExecutor(IntentRouter(router), router, s.runner))
    t0 = time.time()
    tid = int(s.tasks.store.create(title=drill.REQUEST, request_text=drill.REQUEST,
                                   task_type="user_request", channel="telegram",
                                   chat_id="555").id)
    for _ in range(4):
        if not await s.tasks.engine.run_once():
            break
    t = s.tasks.store.get(tid)
    print(f"after the AI work: {t.state.value} in {time.time() - t0:.0f}s; "
          f"approval buttons: {len(s.tasks.buttons)}")
    report = next((txt for _, txt in s.tasks.sent if "nova/task-" in txt), "")
    print("--- report the owner sees before approving ---\n" + report[:2000])
    if t.state.value != "WAITING_APPROVAL" or not s.tasks.buttons:
        print("PHASE 15 GIT DRILL: FAIL (no push approval asked)")
        return 1
    nonce = s.tasks.last_callback(True).split(":")[1]
    s.tasks.approvals.decide(nonce, True, "555")              # the drill stands in for the owner
    for _ in range(4):
        if not await s.tasks.engine.run_once():
            break
    t = s.tasks.store.get(tid)
    branch = f"nova/task-{tid}"
    print(f"after approval: {t.state.value}\n{(t.result_summary or t.error_message or '')[-600:]}")
    check = Path(tempfile.mkdtemp(prefix="nova-git-check-"))
    subprocess.run([GIT, "clone", "-q", "-b", branch, str(remote), str(check)],  # noqa: S603
                   check=False)
    tests = subprocess.run([sys.executable, "-m", "unittest"], cwd=check,  # noqa: S603
                           capture_output=True, text=True)
    main_unchanged = git(remote, "show", f"{main_branch}:stats.py") == drill.STATS
    ok = (t.state.value == "COMPLETED" and tests.returncode == 0 and main_unchanged
          and calls["n"] == 1)
    print(f"remote {branch}: tests {'PASS' if tests.returncode == 0 else 'FAIL'}; "
          f"origin/{main_branch} unchanged: {main_unchanged}; AI runs: {calls['n']}")
    shutil.rmtree(check, ignore_errors=True)
    print(f"PHASE 15 GIT DRILL: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
