"""Coding flow (plan §35, Phase 12): a coding request on a registered project
→ the router's coding provider (Codex today; Antigravity/Claude later, same
flow) edits the project → Nova verifies with the project's own tests.

Safety (owner choice 2026-09-26, permission `code.edit` YELLOW):
- only a folder registered in config/projects (never Nova's own app/data/
  secrets folders);
- automatic only when it is a git repo with no uncommitted changes — every
  edit is then visible in `git diff` and undone with one command; otherwise
  the owner approves first;
- the coding agent writes only inside that folder, with no network, and never
  commits or pushes (commit = Phase 15, push always needs approval).
Verification is deterministic (plan §54): the project's test command, run by
Nova (`test.run`), decides — not the AI's own claim. One follow-up round when
the tests still fail.
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from core.config.schema import AppConfig, ProjectProfile
from core.orchestrator.lang import reply_language
from core.queue.engine import TaskBlocked, TaskContext
from core.skills.api import PolicyDenied
from models.router import ProviderRouter
from providers.provider_base import Limits, ProviderRequest

CODING_SYSTEM = """You are working on the owner's software project "{name}" in the
current folder. Make the smallest correct change that does what the owner asks.
Rules: do not commit, push, change git settings or install packages; there is no
network. Do not touch files outside this folder. If you can, run the tests
({test}) to check your change. Finish with a short summary (2-4 sentences) of
what you changed and why, written in {language}."""

TEXT = {
    "bn": {
        "which": "কোন project-এ কাজটা করব? এগুলো set up করা আছে: {names}. (নতুন project যোগ "
                 "করতে config/projects-এ তার folder লিখে দিতে হবে।)",
        "no_folder": "'{name}' project-এর local folder এখনো config-এ দেওয়া নেই, তাই কাজ শুরু "
                     "করিনি।",
        "done": "💻 {name}: কাজ শেষ",
        "failed": "💻 {name}: পরিবর্তন করেছি, কিন্তু test এখনো পাস করছে না",
        "nothing": "💻 {name}: কোনো ফাইল বদলানো হয়নি",
        "agent": "🤖 {provider} যা বলল:",
        "files": "📂 বদলানো ফাইল:",
        "tests_ok": "🧪 Test পাস ✅ ({cmd})",
        "tests_fail": "🧪 Test ফেল ❌ ({cmd}):",
        "no_tests": "🧪 এই project-এ test command দেওয়া নেই — নিজে দেখে নেবেন।",
        "undo": "↩️ বাতিল করতে: git -C \"{folder}\" checkout -- .  (commit/push করিনি — push-এর "
                "আগে সবসময় আপনার অনুমতি লাগবে)",
        "summary": "{name}-এ কোড বদলানো: \"{goal}\"",
        "committed": "⚠️ {provider} নিজে থেকে commit করে ফেলেছে, যেটা নিষেধ ছিল। কিছু push হয়নি। "
                     "আগের অবস্থায় ফিরতে: git -C \"{folder}\" reset --soft {head}",
    },
    "en": {
        "which": "Which project should I work on? Set up: {names}. (To add one, put its folder "
                 "in config/projects.)",
        "no_folder": "The '{name}' project has no local folder in the config yet, so I didn't "
                     "start.",
        "done": "💻 {name}: done",
        "failed": "💻 {name}: changed the code, but the tests still fail",
        "nothing": "💻 {name}: no files were changed",
        "agent": "🤖 {provider} says:",
        "files": "📂 Files changed:",
        "tests_ok": "🧪 Tests passed ✅ ({cmd})",
        "tests_fail": "🧪 Tests failed ❌ ({cmd}):",
        "no_tests": "🧪 This project has no test command — please check it yourself.",
        "undo": "↩️ To undo: git -C \"{folder}\" checkout -- .  (not committed or pushed — a push "
                "always needs your approval)",
        "summary": "Code change in {name}: \"{goal}\"",
        "committed": "⚠️ {provider} made a commit on its own, which it must not do. Nothing was "
                     "pushed. To go back: git -C \"{folder}\" reset --soft {head}",
    },
}


def find_project(cfg: AppConfig, text: str) -> tuple[str, ProjectProfile] | None:
    low = text.lower()
    for key, p in cfg.projects.items():
        names = {key, key.replace("-", " "), p.name.lower(), p.name.lower().replace(" ", "")}
        if any(re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", low) for n in names if n):
            return key, p
    return None


def forbidden_roots(cfg: AppConfig) -> list[Path]:
    """Folders a coding agent may never edit: Nova itself and its private data."""
    return [cfg.app_dir, *(cfg.path(k) for k in ("data_dir", "secrets_dir", "sessions_dir",
                                                  "logs_dir", "backups_dir"))]


async def git(folder: Path, *args: str, timeout: float = 60) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(folder), *args, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    return proc.returncode or 0, out.decode("utf-8", "replace")


async def _head(folder: Path) -> str:
    code, out = await git(folder, "rev-parse", "HEAD")
    return out.strip() if code == 0 else ""


async def git_clean(folder: Path) -> tuple[bool, str]:
    code, out = await git(folder, "rev-parse", "--is-inside-work-tree")
    if code != 0 or out.strip() != "true":
        return False, "not a git repository — changes could not be reviewed/undone"
    code, out = await git(folder, "status", "--porcelain")
    if code != 0:
        return False, "git status failed"
    if out.strip():
        return False, f"uncommitted changes present ({len(out.splitlines())} file(s))"
    return True, "clean git working tree — every change will be visible in git diff"


def test_argv(command: str) -> list[str]:
    argv = shlex.split(command, posix=False)
    if argv and argv[0].lower() in ("python", "python3", "py"):
        argv[0] = sys.executable          # never the Windows Store stub
    return argv


async def run_tests(folder: Path, command: str, timeout: float = 600) -> tuple[bool, str]:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}     # tests must not dirty the tree
    proc = await asyncio.create_subprocess_exec(
        *test_argv(command), cwd=str(folder), stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return False, f"tests timed out after {timeout:.0f}s"
    return proc.returncode == 0, out.decode("utf-8", "replace")


class CodingFlow:
    def __init__(self, cfg: AppConfig, providers: ProviderRouter) -> None:
        self.cfg = cfg
        self.providers = providers

    async def run(self, ctx: TaskContext) -> dict[str, Any]:
        goal = ctx.task.request_text
        lang = reply_language(goal)
        t = TEXT[lang]
        found = find_project(self.cfg, goal)
        if found is None:
            names = ", ".join(p.name for p in self.cfg.projects.values()) or "—"
            return {"kind": "coding", "ok": False, "needs_input": True,
                    "answer": t["which"].format(names=names)}
        key, project = found
        if not project.local_folder:
            return {"kind": "coding", "ok": False, "needs_input": True,
                    "answer": t["no_folder"].format(name=project.name)}
        folder = Path(project.local_folder).resolve()
        if any(folder == r.resolve() or r.resolve() in folder.parents
               for r in forbidden_roots(self.cfg)):
            raise PolicyDenied(f"{folder} is one of Nova's own folders — never edited by an AI")
        if not folder.is_dir():
            return {"kind": "coding", "ok": False, "needs_input": True,
                    "answer": t["no_folder"].format(name=project.name)}

        await ctx.require("code.edit", str(folder),
                          summary=t["summary"].format(name=project.name, goal=goal[:200]),
                          details={"project": key, "request": goal[:300]},
                          safety_check=lambda: git_clean(folder))
        head = await _head(folder)
        language = "Bangla (বাংলা)" if lang == "bn" else "English"
        request = ProviderRequest(
            task_id=ctx.task.id, task_type="coding", workspace=str(folder),
            allowed_tools=("code.edit",), risk_level="YELLOW", user_request=goal,
            system=CODING_SYSTEM.format(name=project.name, language=language,
                                        test=project.test_command or "if any"),
            limits=Limits(timeout_seconds=1200, max_output_tokens=4000))
        result = await self.providers.complete(request)
        if not result.ok:
            raise RuntimeError(f"coding provider unavailable: {result.error}")
        agent_text, provider = result.answer, result.provider
        passed, test_out = await self._test(ctx, project, folder)
        if passed is False and await self._changed(folder):
            # one follow-up round with the real test output (plan §8.5: no restart from zero)
            retry = await self.providers.complete(replace(request, previous_attempt=(
                f"Your change was applied, but the tests still fail:\n{test_out[-3000:]}")))
            if retry.ok:
                agent_text = retry.answer or agent_text
                passed, test_out = await self._test(ctx, project, folder)
        if head and await _head(folder) != head:
            # the rules forbid commits (commit = Phase 15, with the owner): stop and say how to undo
            raise TaskBlocked(t["committed"].format(provider=provider, folder=folder,
                                                    head=head[:12]))
        return await self._report(lang, project, folder, provider, agent_text, passed,
                                  test_out, result.session_id)

    async def _test(self, ctx: TaskContext, project: ProjectProfile, folder: Path
                    ) -> tuple[bool | None, str]:
        if not project.test_command:
            return None, ""
        await ctx.require("test.run", f"{folder} :: {project.test_command}")
        return await run_tests(folder, project.test_command)

    async def _changed(self, folder: Path) -> bool:
        _, out = await git(folder, "status", "--porcelain")
        return any(ln.strip() and "__pycache__" not in ln for ln in out.splitlines())

    async def _report(self, lang: str, project: ProjectProfile, folder: Path, provider: str,
                      agent_text: str, passed: bool | None, test_out: str,
                      session: str | None) -> dict[str, Any]:
        t = TEXT[lang]
        _, status = await git(folder, "status", "--porcelain")
        _, stat = await git(folder, "diff", "--stat")
        changed = [ln[3:].strip() for ln in status.splitlines()
                   if ln.strip() and "__pycache__" not in ln]
        head = (t["nothing"] if not changed else t["done"] if passed is not False
                else t["failed"])
        lines = [head.format(name=project.name)]
        if agent_text:
            lines += ["", t["agent"].format(provider=provider), agent_text[:1200]]
        if changed:
            lines += ["", t["files"], *[f"• {c}" for c in changed[:15]]]
            summary = stat.strip().splitlines()[-1:] if stat.strip() else []
            lines += [f"  ({summary[0].strip()})"] if summary else []
        cmd = project.test_command or ""
        if passed is None:
            lines += ["", t["no_tests"]]
        elif passed:
            lines += ["", t["tests_ok"].format(cmd=cmd), _test_tail(test_out, 3)]
        else:
            lines += ["", t["tests_fail"].format(cmd=cmd), _test_tail(test_out, 12)]
        if changed:
            lines += ["", t["undo"].format(folder=folder)]
        ok = bool(changed) and passed is not False
        if changed and passed is False:
            # tests still fail after the follow-up round: the owner decides what next
            raise TaskBlocked("\n".join(lines))
        return {"kind": "coding", "ok": ok, "answer": "\n".join(lines),
                "evidence": {"project": project.name, "provider": provider,
                             "files_changed": changed, "tests_passed": passed,
                             "session_id": session}}


def _test_tail(output: str, n: int) -> str:
    rows = [ln.rstrip() for ln in output.strip().splitlines() if ln.strip()]
    return "\n".join(f"   {ln}" for ln in rows[-n:])
