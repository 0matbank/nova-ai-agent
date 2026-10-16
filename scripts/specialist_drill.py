"""Phase 16 live drill — specialist + project agent routing with the REAL providers
(local qwen3 plans the tool steps; nothing here changes the PC or opens an app):

  1. File & Document agent  — count files in a scratch folder (files/search skills)
  2. Windows Operator agent — which app uses the most memory (read-only)
  3. Security agent         — look for a leaked key in a scratch project (read-only)
  4. Project specialist     — "Drill Demo test koro" runs the project's own tests, no AI
  5. Git agent              — "drill demo-r git status dekhao", no AI

    uv run python scripts/specialist_drill.py

PASS = every request reaches the expected agent + route, finishes COMPLETED, and
answers 1–3 contain the expected facts.
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


def git(repo: Path, *args: str) -> None:
    subprocess.run([GIT, "-C", str(repo), *args], check=True, capture_output=True)  # noqa: S603


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="nova-specialist-drill-"))
    try:
        return await run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def run(tmp: Path) -> int:
    box = tmp / "inbox"
    box.mkdir()
    for n in ("bill.pdf", "cv.pdf", "notes.txt", "photo.jpg", "plan.pdf"):
        (box / n).write_text("x", encoding="utf-8")
    repo = tmp / "drill-demo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "drill")
    git(repo, "config", "user.email", "drill@example.invalid")
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (repo / "test_calc.py").write_text(
        "import unittest\nfrom calc import add\n\n\nclass T(unittest.TestCase):\n"
        "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    fake_key = "AIza" + "Q" * 35                       # shaped like a key, not a real one
    (repo / "settings.py").write_text(f'API_KEY = "{fake_key}"\n', encoding="utf-8")

    s = make_skill_env(tmp)
    (tmp / "config" / "projects" / "drill-demo.yaml").write_text(
        f"name: Drill Demo\nlocal_folder: '{repo}'\ntest_command: \"python -m unittest -v\"\n",
        encoding="utf-8")
    cfg_text = (tmp / "config" / "agents.yaml").read_text(encoding="utf-8")
    (tmp / "config" / "agents.yaml").write_text(
        cfg_text.replace("project_agents: [", "project_agents: [drill-demo, "), encoding="utf-8")
    cfg = load_config(tmp / "config", tmp / "rt")
    s.runner.env.config = cfg
    adapters = ProviderRegistry.from_config(
        cfg, load_secrets(load_config().path("secrets_dir"))).adapters
    router = ProviderRouter(cfg.providers, adapters)
    s.tasks.engine.register("user_request",
                            UserRequestExecutor(IntentRouter(router), router, s.runner))

    cases = [
        (f"{box} folder e koyta pdf file ache? naam gulo bolo", "file_document", "operator",
         ["3", "bill", "cv", "plan"]),
        # a fixed read-only skill does this — no AI at all (plan §45)
        ("PC te kon app sobcheye beshi RAM khacche? process list theke bolo",
         "windows_operator", "skill", ["MB"]),
        ("C drive e koto GB jayga free ache dekho to", "windows_operator",
         "operator", []),
        (f"{repo} folder e kono secret leak ache kina check koro", "security", "operator",
         ["settings.py"]),
        ("Drill Demo test koro", "project:drill-demo", "project_test", ["OK"]),
        ("drill demo er git status dekhao", "git", "git", ["settings.py"]),
    ]
    # the security answer must REPORT the leak, not call the masked key safe
    leak_words = ("leak", "লিক", "ফাঁস", "secret", "সিক্রেট", "গোপন", "key", "কী")
    safe_words = ("no leak", "no secret", "not leaked", "safe", "নিরাপদ", "লিক নেই",
                  "পাওয়া যায়নি", "already redacted")
    ok_all = True
    for text, lead, route, facts in cases:
        t0 = time.time()
        tid = int(s.tasks.store.create(title=text, request_text=text,
                                       task_type="user_request", channel="telegram",
                                       chat_id="555").id)
        for _ in range(4):
            if not await s.tasks.engine.run_once():
                break
        t = s.tasks.store.get(tid)
        step1 = next(st for st in t.steps if st.seq == 1)
        a = (step1.checkpoint or {}).get("assignment") or {}
        answer = t.result_summary or t.error_message or ""
        plain = answer.translate(str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")).lower()
        hits = [f for f in facts if f.lower() not in plain]
        if "drive" in text and not any(u in answer for u in ("GB", "জিবি", "গিগাবাইট")):
            hits.append("free space in GB")
        if lead == "security":
            low = answer.lower()
            if not any(w in low for w in leak_words):
                hits.append("does not report the leak")
            hits += [f"says {w!r}" for w in safe_words if w in low]
        ok = (a.get("lead") == lead and a.get("route") == route
              and t.state.value == "COMPLETED" and not hits)
        ok_all &= ok
        print(f"\n[{'PASS' if ok else 'FAIL'}] {text[:70]}\n  → {a.get('agents')} "
              f"route={a.get('route')} ai={a.get('provider_task_type')} "
              f"{t.state.value} in {time.time() - t0:.0f}s"
              + (f"\n  missing facts: {hits}" if hits else "")
              + f"\n  answer: {answer[:400]}")
        if not ok:
            for _, msg in [m for m in s.tasks.sent if f"#{tid}" in m[1]][-2:]:
                print("  sent: " + msg[:500].replace("\n", " | "))
            step2 = next((st for st in t.steps if st.seq == 2), None)
            partial = ((step2.checkpoint or {}) if step2 else {}).get("partial") or {}
            for st in partial.get("steps", []):
                print(f"  step: {st['call'][:150]} → {str(st['result'])[:250]}"
                      .replace("\n", " | "))
    print(f"\nPHASE 16 SPECIALIST DRILL: {'PASS' if ok_all else 'FAIL'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
