"""Phase 14 live drill — provider failover mid-task (plan §8.5, Phase 14 pass):

  real Codex fixes ONE of the two bugs, then "dies" (a simulated 503 after its
  real work) → the router opens Codex's circuit and hands the task to real
  Antigravity with the failure summary + the diff so far → Antigravity finishes
  the remaining bug → Nova runs the project's tests.

    uv run python scripts/failover_drill.py

PASS = task COMPLETED, both of Codex's and Antigravity's edits are in the file,
tests pass, nothing committed, the report says who took over, and Codex's
circuit is open (it is not called again for the next task).
"""

import asyncio
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import load_config, load_secrets  # noqa: E402
from core.orchestrator.executor import UserRequestExecutor  # noqa: E402
from core.router.intent import IntentRouter  # noqa: E402
from models.router import ProviderRouter  # noqa: E402
from providers.provider_base import (  # noqa: E402
    ErrorCategory,
    Health,
    ProviderAdapter,
    ProviderError,
    ProviderRequest,
    ProviderResult,
)
from providers.registry import ProviderRegistry  # noqa: E402
from tests.mocks.skills import make_skill_env  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "coding_drill", Path(__file__).with_name("coding_drill.py"))
assert _spec and _spec.loader
drill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(drill)
GIT = shutil.which("git") or "git"


class DiesMidway(ProviderAdapter):
    """Wraps the real Codex: the first call does real work on part of the task,
    then reports a 503 as if the service fell over; later calls fail at once."""

    def __init__(self, real: ProviderAdapter) -> None:
        super().__init__()
        self.real = real
        self.name = real.name
        self.capabilities = real.capabilities
        self.calls = 0

    async def check_health(self) -> Health:
        self.health = await self.real.check_health()
        return self.health

    async def _complete(self, request: ProviderRequest) -> ProviderResult:
        self.calls += 1
        if self.calls == 1:
            part = replace(request, user_request=request.user_request + "\n\nFor now fix ONLY "
                           "average() — leave median() exactly as it is.")
            done = await self.real.complete(part)
            print(f"  codex (real) did part of the work: ok={done.ok}")
        raise ProviderError(ErrorCategory.SERVER_ERROR, "503 service overloaded (simulated "
                                                        "crash after partial work)")


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="nova-failover-drill-"))
    repo = load_config().path("workspace_dir") / f".failover-demo-{tmp.name[-8:]}"
    drill.make_repo(repo)
    try:
        return await run(tmp, repo)
    finally:
        shutil.rmtree(repo, ignore_errors=True)


async def run(tmp: Path, repo: Path) -> int:
    s = make_skill_env(tmp)
    (tmp / "config" / "projects" / "drill-demo.yaml").write_text(
        f"name: Drill Demo\nlocal_folder: '{repo}'\ntest_command: \"python -m unittest -v\"\n",
        encoding="utf-8")
    cfg = load_config(tmp / "config", tmp / "rt")
    s.runner.env.config = cfg
    adapters = ProviderRegistry.from_config(
        cfg, load_secrets(load_config().path("secrets_dir"))).adapters
    codex = DiesMidway(adapters["openai_codex"])
    adapters["openai_codex"] = codex
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
    print(f"{t.state.value} in {time.time() - t0:.0f}s")
    print((t.result_summary or f"{t.error_code}: {t.error_message}")[:2500])
    body = (repo / "stats.py").read_text(encoding="utf-8")
    tests = subprocess.run([sys.executable, "-m", "unittest"], cwd=repo,  # noqa: S603
                           capture_output=True, text=True)
    log = subprocess.run([GIT, "-C", str(repo), "log", "--oneline"],  # noqa: S603
                         capture_output=True, text=True).stdout.strip().splitlines()
    open_min = router.breaker.open_for("openai_codex") / 60
    took_over = "🔁 openai_codex" in (t.result_summary or "")
    print(f"independent test run: {'PASS' if tests.returncode == 0 else 'FAIL'}; commits: "
          f"{len(log)}; codex calls: {codex.calls}; codex circuit open: {open_min:.0f} min; "
          f"report names the takeover: {took_over}")
    print("final stats.py:\n" + body)
    ok = (t.state.value == "COMPLETED" and tests.returncode == 0 and len(log) == 1
          and took_over and open_min > 0 and "google_antigravity" in (t.result_summary or ""))
    print(f"PHASE 14 FAILOVER DRILL: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
