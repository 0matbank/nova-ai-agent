"""Phase 18 live drill — multi-step work completes only with independent verification,
using the REAL providers (scratch folder + scratch repo; nothing else is touched):

  1. File agent (local qwen3) writes a file → the verifier checks the PC itself
  2. A security fix in a scratch repo: Codex implements → Antigravity (a DIFFERENT
     provider, read-only) reviews → Nova runs the tests → commit on nova/task-<id>
     → the verifier checks the commit with git itself

    uv run python scripts/verifier_drill.py

PASS = both COMPLETED with a ✅ PASS verdict; (1) the file really exists with the
content; (2) the reviewer was not the implementer and saw only the diff, the branch
holds the fix, main is untouched.
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

GIT = shutil.which("git") or "git"
AUTH = '''def check(password):
    """True when the password is right."""
    return password == "admin123"
'''
TESTS = '''import os
import unittest
from unittest import mock

from auth import check


class T(unittest.TestCase):
    def test_wrong_password(self):
        with mock.patch.dict(os.environ, {"APP_PASSWORD": "s3cret"}):
            self.assertFalse(check("nope"))

    def test_right_password_from_environment(self):
        with mock.patch.dict(os.environ, {"APP_PASSWORD": "s3cret"}):
            self.assertTrue(check("s3cret"))

    def test_old_hardcoded_password_no_longer_works(self):
        with mock.patch.dict(os.environ, {"APP_PASSWORD": "s3cret"}):
            self.assertFalse(check("admin123"))
'''


def git(repo: Path, *args: str) -> str:
    return subprocess.run([GIT, "-C", str(repo), *args], capture_output=True,  # noqa: S603
                          text=True).stdout


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="nova-verifier-drill-"))
    base = load_config().path("workspace_dir") / f".verifier-drill-{tmp.name[-8:]}"
    try:
        return await run(tmp, base)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(base, ignore_errors=True)


async def run(tmp: Path, base: Path) -> int:
    repo = base / "auth-demo"                 # Codex cannot write under %TEMP% (Phase 12)
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "drill")
    git(repo, "config", "user.email", "drill@example.invalid")
    (repo / "auth.py").write_text(AUTH, encoding="utf-8")
    (repo / "test_auth.py").write_text(TESTS, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base: hardcoded password")
    s = make_skill_env(tmp)
    (tmp / "config" / "projects" / "auth-demo.yaml").write_text(
        f"name: Auth Demo\nlocal_folder: '{repo}'\ntest_command: \"python -m unittest -v\"\n",
        encoding="utf-8")
    agents = (tmp / "config" / "agents.yaml").read_text(encoding="utf-8")
    (tmp / "config" / "agents.yaml").write_text(
        agents.replace("project_agents: [", "project_agents: [auth-demo, "), encoding="utf-8")
    cfg = load_config(tmp / "config", tmp / "rt")
    s.runner.env.config = cfg
    adapters = ProviderRegistry.from_config(
        cfg, load_secrets(load_config().path("secrets_dir"))).adapters
    calls: list[tuple[str, str]] = []
    for name in ("openai_codex", "google_antigravity"):
        real = adapters[name].complete

        async def counted(request, _real=real, _name=name):  # type: ignore[no-untyped-def]
            calls.append((_name, request.task_type))
            return await _real(request)
        adapters[name].complete = counted  # type: ignore[method-assign]
    router = ProviderRouter(cfg.providers, adapters)
    s.tasks.engine.register("user_request",
                            UserRequestExecutor(IntentRouter(router), router, s.runner))

    async def ask(text: str) -> tuple[int, str, str]:
        t0 = time.time()
        tid = int(s.tasks.store.create(title=text, request_text=text, task_type="user_request",
                                       channel="telegram", chat_id="555").id)
        for _ in range(6):
            if not await s.tasks.engine.run_once():
                break
        t = s.tasks.store.get(tid)
        done = next((m for _, m in s.tasks.sent if f"TASK #{tid} COMPLETE" in m), "")
        verdict = done.split("Verifier:", 1)[-1].strip() if done else (t.error_message or "")
        print(f"\n> {text}\n  {t.state.value} in {time.time() - t0:.0f}s\n  verdict: "
              f"{verdict[:600]}")
        return tid, t.state.value, verdict

    note = s.runner.env.config.path("workspace_dir") / "drill-note.txt"
    _, st1, v1 = await ask(f"{note} file e 'hello nova' likhe dao")
    file_ok = note.is_file() and "hello nova" in note.read_text(encoding="utf-8")
    tid, st2, v2 = await ask("auth demo te auth.py er security fix koro: hardcoded password "
                             "sorao, password APP_PASSWORD environment variable theke porbe")
    branch = f"nova/task-{tid}"
    fixed = git(repo, "show", f"{branch}:auth.py")
    main_same = git(repo, "show", "main:auth.py") == AUTH
    reviewers = [n for n, kind in calls if kind == "review"]
    implementers = [n for n, kind in calls if kind == "coding"]
    ok = (st1 == st2 == "COMPLETED" and file_ok and "✅ PASS" in v1 and "✅ PASS" in v2
          and bool(reviewers) and not set(reviewers) & set(implementers)
          and "admin123" not in fixed and "APP_PASSWORD" in fixed and main_same)
    print(f"\nfile really written: {file_ok}; implementer {implementers}; reviewers "
          f"{reviewers}; branch fixed: {'APP_PASSWORD' in fixed}; main untouched: {main_same}")
    print(f"PHASE 18 VERIFIER DRILL: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
