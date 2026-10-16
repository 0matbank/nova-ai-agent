"""Phase 17 live drill — project context persists between tasks, with the REAL local
model (nothing on the PC changes; a scratch project and a scratch DB):

  1. "Drill Demo test koro"            → tests fail → open issue in its memory
  2. "drill demo te mone rakho: …"     → the owner's decision in its memory
  3. a NEW executor (= after a restart): "drill demo project er obostha ki?"
     → the AI's answer uses the remembered failing tests and the decision

    uv run python scripts/project_memory_drill.py

PASS = 1–3 COMPLETED; the memory holds the issue + decision; answer 3 mentions
the failing tests and the decision; no other project's memory reached the AI.
"""

import asyncio
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.agents import Roster  # noqa: E402
from core.config import load_config, load_secrets  # noqa: E402
from core.memory import ProjectMemory  # noqa: E402
from core.orchestrator.executor import UserRequestExecutor  # noqa: E402
from core.router.intent import IntentRouter  # noqa: E402
from models.router import ProviderRouter  # noqa: E402
from providers.registry import ProviderRegistry  # noqa: E402
from tests.mocks.skills import make_skill_env  # noqa: E402

GIT = shutil.which("git") or "git"


def git(repo: Path, *args: str) -> None:
    subprocess.run([GIT, "-C", str(repo), *args], check=True, capture_output=True)  # noqa: S603


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="nova-memory-drill-"))
    try:
        return await run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def run(tmp: Path) -> int:
    repo = tmp / "drill-demo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "prices.py").write_text("def total(items):\n    return sum(items) - 1\n",
                                    encoding="utf-8")
    (repo / "test_prices.py").write_text(
        "import unittest\nfrom prices import total\n\n\nclass T(unittest.TestCase):\n"
        "    def test_total(self):\n        self.assertEqual(total([2, 3]), 5)\n",
        encoding="utf-8")
    s = make_skill_env(tmp)
    (tmp / "config" / "projects" / "drill-demo.yaml").write_text(
        f"name: Drill Demo\nlocal_folder: '{repo}'\ntest_command: \"python -m unittest -v\"\n",
        encoding="utf-8")
    agents = (tmp / "config" / "agents.yaml").read_text(encoding="utf-8")
    (tmp / "config" / "agents.yaml").write_text(
        agents.replace("project_agents: [", "project_agents: [drill-demo, "), encoding="utf-8")
    cfg = load_config(tmp / "config", tmp / "rt")
    s.runner.env.config = cfg
    secrets = load_secrets(load_config().path("secrets_dir"))

    def executor() -> tuple[UserRequestExecutor, ProjectMemory]:
        adapters = ProviderRegistry.from_config(cfg, secrets).adapters
        router = ProviderRouter(cfg.providers, adapters)
        memory = ProjectMemory(s.tasks.store._sessions)
        return UserRequestExecutor(IntentRouter(router), router, s.runner, Roster.load(cfg),
                                   memory), memory

    async def ask(text: str) -> tuple[str, str]:
        t0 = time.time()
        tid = int(s.tasks.store.create(title=text, request_text=text, task_type="user_request",
                                       channel="telegram", chat_id="555").id)
        for _ in range(4):
            if not await s.tasks.engine.run_once():
                break
        t = s.tasks.store.get(tid)
        answer = t.result_summary or t.error_message or ""
        print(f"\n> {text}\n  {t.state.value} in {time.time() - t0:.0f}s\n  "
              + answer[:500].replace("\n", "\n  "))
        return t.state.value, answer

    ex, memory = executor()
    memory.put("click-tv", "decision", "CLICK-TV-ONLY rule — must not reach Drill Demo")
    s.tasks.engine.register("user_request", ex)
    st1, _ = await ask("Drill Demo test koro")
    st2, _ = await ask("drill demo te mone rakho: sobsomoy prices.py te type hints rakhbe")
    issues = memory.entries("drill-demo", ("known_issue",))
    decisions = memory.entries("drill-demo", ("decision",))
    print(f"\nmemory: {len(issues)} open issue(s), {len(decisions)} decision(s)")
    ex2, _ = executor()                                   # "after a restart"
    s.tasks.engine.register("user_request", ex2)
    st3, a3 = await ask("drill demo project er ekhon ki obostha? age ki hoyechilo ar ki "
                        "decision ache?")
    low = a3.lower()
    mentions_tests = any(w in low for w in ("test", "টেস্ট", "fail", "ফেল", "ব্যর্থ"))
    mentions_decision = any(w in low for w in ("type hint", "টাইপ হিন্ট", "type-hint"))
    leaked = "click-tv-only" in low
    ok = (st1 == st2 == st3 == "COMPLETED" and len(issues) == 1 and len(decisions) == 1
          and mentions_tests and mentions_decision and not leaked)
    print(f"\nanswer 3 mentions the failing tests: {mentions_tests}; the decision: "
          f"{mentions_decision}; other project's memory leaked: {leaked}")
    print(f"PHASE 17 PROJECT MEMORY DRILL: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
