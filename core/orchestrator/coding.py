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
import hashlib
import os
import re
import shlex
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from core.config.schema import AppConfig, ProjectProfile
from core.notify.notifier import MessageType
from core.orchestrator.lang import reply_language
from core.queue.engine import TaskBlocked, TaskContext
from core.skills.api import PolicyDenied
from core.skills.runner import SkillRunner
from models.router import ProviderRouter
from models.router.router import Handoff, default_handoff
from providers.provider_base import Limits, ProviderRequest, ProviderResult

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
        "owner_changes": "ℹ️ কাজ শুরুর সময় repo-তে আপনার নিজের কিছু uncommitted পরিবর্তন "
                         "ছিল — সেগুলো যেন Nova-র commit-এ না ঢোকে, তাই commit/push করিনি। "
                         "পরিবর্তন working tree-তেই আছে।",
        "pull_failed": "⚠️ git pull হয়নি ({reason}) — local কপির ওপর কাজ করেছি; push আলাদা "
                       "branch-এ যাবে, তাই main-এ কিছু বদলাবে না।",
        "build_ok": "🏗️ Build ঠিক আছে ✅ ({cmd})",
        "build_fail": "🏗️ Build ফেল ❌ ({cmd}):",
        "on_branch": "📌 পরিবর্তন {branch} branch-এ commit করা ({commit}) — আপনার main/চলতি "
                     "branch যেমন ছিল তেমনই আছে।\n   দেখতে: git -C \"{folder}\" checkout {branch}",
        "no_remote": "ℹ️ এই project-এর কোনো remote নেই — commit PC-তেই রইল, push হয়নি।",
        "push_ask": "⬆️ এটা remote-এ push করতে নিচের Approve চাপুন (merge আপনি নিজে করবেন)।",
        "pushed": "🚀 Push হয়েছে: {remote} → {branch}। Main-এ merge আপনার সিদ্ধান্ত।",
        "push_failed": "❌ Push হয়নি: {reason}",
        "commit_failed": "❌ Commit হয়নি: {reason}",
        "working": "💻 {name}: কোড বদলানোর কাজ চলছে — কয়েক মিনিট লাগতে পারে। থামাতে /cancel",
        "follow_up": "🧪 Test এখনো ফেল — আরেকবার ঠিক করার চেষ্টা চলছে…",
        "switched": "🔁 {failed} মাঝপথে থেমে গিয়েছিল — {provider} আগের কাজ থেকেই বাকিটা শেষ করেছে।",
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
        "owner_changes": "ℹ️ The repo had uncommitted work of yours when I started — so it "
                         "cannot end up in Nova's commit, I did not commit or push. The "
                         "changes are in the working tree.",
        "pull_failed": "⚠️ git pull did not run ({reason}) — worked on the local copy; the "
                       "push goes to its own branch, so main is not touched.",
        "build_ok": "🏗️ Build OK ✅ ({cmd})",
        "build_fail": "🏗️ Build failed ❌ ({cmd}):",
        "on_branch": "📌 Committed on {branch} ({commit}) — your main/current branch is "
                     "unchanged.\n   To look: git -C \"{folder}\" checkout {branch}",
        "no_remote": "ℹ️ This project has no remote — the commit stays on this PC, not pushed.",
        "push_ask": "⬆️ Tap Approve below to push it (merging stays with you).",
        "pushed": "🚀 Pushed: {remote} → {branch}. Merging into main is your call.",
        "push_failed": "❌ Push failed: {reason}",
        "commit_failed": "❌ Commit failed: {reason}",
        "working": "💻 {name}: changing the code — this can take a few minutes. /cancel stops it",
        "follow_up": "🧪 Tests still fail — trying once more…",
        "switched": "🔁 {failed} stopped midway — {provider} finished it from where it left off.",
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


async def _tree_state(folder: Path) -> str:
    _, stat = await git(folder, "diff", "--stat")
    _, diff = await git(folder, "diff")
    return ("No files have been changed yet." if not diff.strip() else
            "Changes already made in the working tree — keep them and build on them:\n"
            f"{stat.strip()}\n\n{diff[-6000:]}")


async def _tree_fingerprint(folder: Path) -> str:
    """HEAD + status + diff: proves the tree holds exactly what this task left."""
    _, status = await git(folder, "status", "--porcelain")
    _, diff = await git(folder, "diff")
    rows = [ln for ln in status.splitlines() if "__pycache__" not in ln]
    blob = "\n".join([await _head(folder), *rows, diff])
    return hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()


async def _remember(ctx: TaskContext, folder: Path, switched: list[str], error: str,
                    head: str, owner_changes: bool) -> None:
    """Portable state in the task's checkpoint (plan §9.2): survives retry and restart."""
    ctx.remember({"failed": list(dict.fromkeys(switched)), "error": error[:500],
                  "head": head, "tree": await _tree_fingerprint(folder),
                  "owner_changes": owner_changes})


def _handoff(folder: Path, switched: list[str], ctx: TaskContext, head: str,
             owner_changes: bool) -> Handoff:
    """Provider failover mid-task (plan §8.5): the next provider gets the failure
    summary plus the edits already in the working tree, and builds on them."""
    async def handoff(request: ProviderRequest, failed: str,
                      result: ProviderResult) -> ProviderRequest:
        switched.append(failed)
        await _remember(ctx, folder, switched, result.error or "", head, owner_changes)
        request = await default_handoff(request, failed, result)
        state = await _tree_state(folder)
        return replace(request, previous_attempt=f"{request.previous_attempt}\n\n{state}")
    return handoff


async def _file_list(folder: Path, limit: int = 200) -> str:
    """The project's tracked files, so any coding agent starts from the real layout."""
    code, out = await git(folder, "ls-files")
    files = [f for f in out.splitlines() if f.strip()] if code == 0 else []
    if not files:
        return ""
    more = f"\n… and {len(files) - limit} more" if len(files) > limit else ""
    return "Files in this project (git ls-files):\n" + "\n".join(files[:limit]) + more


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
    """Plan §35 / Phase 15: pull → AI edit → build → test → commit on nova/task-<id>
    → report with the diff → owner approves → push. Each finished phase is saved in
    the task checkpoint, so resuming after an approval never re-runs the AI."""

    def __init__(self, cfg: AppConfig, providers: ProviderRouter,
                 skills: SkillRunner | None = None) -> None:
        self.cfg = cfg
        self.providers = providers
        self.skills = skills

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

        # an earlier attempt of this same task may have stopped mid-way (provider down,
        # restart): its edits are still in the tree — continue from them (plan §9.2)
        earlier = ctx.recalled()
        if earlier and earlier.get("phase") in ("verified", "committed"):
            # resumed after an approval (commit with a secret warning / push): the AI
            # work and the tests are done — go on from there
            return await self._deliver(ctx, lang, key, project, folder, earlier)
        # the owner's own uncommitted work must never end up in Nova's commit
        owner_changes = bool(earlier.get("owner_changes")) if earlier else \
            await self._changed(folder)
        pull_note = "" if earlier else await self._pull(ctx, key, lang)

        async def safe() -> tuple[bool, str]:
            if earlier and earlier.get("tree") == await _tree_fingerprint(folder):
                return True, "only this task's own unfinished changes from an earlier attempt"
            return await git_clean(folder)

        await ctx.require("code.edit", str(folder),
                          summary=t["summary"].format(name=project.name, goal=goal[:200]),
                          details={"project": key, "request": goal[:300]},
                          safety_check=safe)
        head = str(earlier["head"]) if earlier and earlier.get("head") else await _head(folder)
        language = "Bangla (বাংলা)" if lang == "bn" else "English"
        request = ProviderRequest(
            task_id=ctx.task.id, task_type="coding", workspace=str(folder),
            allowed_tools=("code.edit",), risk_level="YELLOW", user_request=goal,
            context=await _file_list(folder),
            system=CODING_SYSTEM.format(name=project.name, language=language,
                                        test=project.test_command or "if any"),
            limits=Limits(timeout_seconds=1200, max_output_tokens=4000))
        switched: list[str] = list(earlier.get("failed", [])) if earlier else []
        if earlier:
            request = replace(request, previous_attempt=(
                f"An earlier attempt of this task stopped ({', '.join(switched) or 'interrupted'}"
                f": {str(earlier.get('error', ''))[:300]}).\n"
                + await _tree_state(folder)))
        handoff = _handoff(folder, switched, ctx, head, owner_changes)
        if not earlier:
            # a restart mid-run must still know the starting point (plan §9.2)
            await _remember(ctx, folder, switched, "", head, owner_changes)
        await ctx.progress(t["working"].format(name=project.name))
        result = await ctx.interruptible(self.providers.complete(request, handoff=handoff))
        if not result.ok:
            await _remember(ctx, folder, switched, result.error or "", head, owner_changes)
            raise RuntimeError(f"coding provider unavailable: {result.error}")
        agent_text, provider = result.answer, result.provider
        built, build_out = await self._build(ctx, project, folder)
        passed, test_out = (False, build_out) if built is False else \
            await self._test(ctx, project, folder)
        if passed is False and await self._changed(folder):
            # one follow-up round with the real build/test output (plan §8.5: no restart)
            await ctx.progress(t["follow_up"])
            retry = await ctx.interruptible(self.providers.complete(replace(
                request, previous_attempt=(
                    f"Your change was applied, but the build/tests still fail:\n"
                    f"{test_out[-3000:]}")),
                handoff=handoff))
            if retry.ok:
                agent_text = retry.answer or agent_text
                built, build_out = await self._build(ctx, project, folder)
                passed, test_out = (False, build_out) if built is False else \
                    await self._test(ctx, project, folder)
        if head and await _head(folder) != head:
            # the rules forbid commits (commit = Phase 15, with the owner): stop and say how to undo
            raise TaskBlocked(t["committed"].format(provider=provider, folder=folder,
                                                    head=head[:12]))
        if switched:
            agent_text = t["switched"].format(failed=", ".join(dict.fromkeys(switched)),
                                              provider=provider) + "\n" + agent_text
        report = await self._report(lang, project, folder, provider, agent_text, passed,
                                    test_out, result.session_id, build_out)
        if pull_note:
            report["answer"] = pull_note + "\n\n" + report["answer"]
        if self.skills is None or not report["ok"] or "git" not in self.skills.registry.skills:
            return report
        state = {"phase": "verified", "report": report["answer"],
                 "evidence": report["evidence"], "goal": goal[:300],
                 "owner_changes": owner_changes}
        ctx.remember(state)
        return await self._deliver(ctx, lang, key, project, folder, state)

    async def _pull(self, ctx: TaskContext, key: str, lang: str) -> str:
        """§35 'Git pull' first: fast-forward only, and only on a clean tree. A pull that
        cannot run (no network, diverged) is reported, not fatal — the work goes on a
        separate branch anyway."""
        if self.skills is None or "git" not in self.skills.registry.skills:
            return ""
        try:
            r = await self.skills.invoke(ctx, "git", "pull", {"project": key})
        except PolicyDenied:
            return ""                        # owner's uncommitted work: leave it alone
        if r.ok:
            return ""
        return TEXT[lang]["pull_failed"].format(reason=r.summary[-200:])

    async def _build(self, ctx: TaskContext, project: ProjectProfile, folder: Path
                     ) -> tuple[bool | None, str]:
        """§35 'Build' before 'Test' — when the project has a build command."""
        if not project.build_command:
            return None, ""
        await ctx.require("test.run", f"{folder} :: {project.build_command}")
        ok, out = await ctx.interruptible(run_tests(folder, project.build_command))
        return ok, ("" if ok else f"BUILD FAILED ({project.build_command}):\n") + out

    async def _deliver(self, ctx: TaskContext, lang: str, key: str, project: ProjectProfile,
                       folder: Path, state: dict[str, Any]) -> dict[str, Any]:
        """Commit on nova/task-<id> (YELLOW, secret check) → report → push (RED: the
        owner approves every push) — owner choice 2026-10-15: never straight to main."""
        assert self.skills is not None
        t = TEXT[lang]
        branch = f"nova/task-{ctx.task.id}"
        report = str(state.get("report", ""))
        if state.get("owner_changes"):
            # the repo had the owner's own uncommitted work: committing would sweep it in
            return {"kind": "coding", "ok": True,
                    "answer": f"{report}\n\n{t['owner_changes']}",
                    "evidence": {**(state.get("evidence") or {}), "committed": False}}
        if state.get("phase") == "verified":
            goal = " ".join(str(state.get("goal", "")).split())
            ev = state.get("evidence") or {}
            message = (f"Nova: {goal[:68]}\n\nTask #{ctx.task.id} via {ev.get('provider')}; "
                       f"tests passed: {ev.get('tests_passed')}.")
            c = await self.skills.invoke(ctx, "git", "commit", {
                "project": key, "message": message, "branch": branch})
            if not c.ok:
                raise TaskBlocked(report + "\n\n" + t["commit_failed"].format(reason=c.summary))
            state = {**state, "phase": "committed", "commit": c.data.get("commit"),
                     "branch": branch, "stat": c.data.get("stat", "")}
            ctx.remember(state)
        commit = str(state.get("commit", ""))[:8]
        lines = [report.split("\n↩️")[0].rstrip(), "",
                 t["on_branch"].format(branch=branch, commit=commit, folder=folder)]
        evidence = {**(state.get("evidence") or {}), "branch": branch,
                    "commit": state.get("commit"), "pushed": False}
        from core.skills.gitops import remote_url
        if await remote_url(folder) is None:
            lines.append(t["no_remote"])
            return {"kind": "coding", "ok": True, "answer": "\n".join(lines),
                    "evidence": evidence}
        if not state.get("asked"):
            # the owner sees what changed before the Approve / Reject buttons arrive
            ctx.remember({**state, "asked": True})
            if ctx.notifier is not None:
                await ctx.notifier.notify(ctx.task.chat_id, MessageType.INFO,
                                          "\n".join([*lines, "", t["push_ask"]]),
                                          task_id=ctx.task.id)
        p = await self.skills.invoke(ctx, "git", "push", {"project": key, "branch": branch})
        if not p.ok:
            raise TaskBlocked("\n".join([*lines, "", t["push_failed"].format(reason=p.summary)]))
        lines.append(t["pushed"].format(branch=branch, remote=p.data.get("remote", "origin")))
        return {"kind": "coding", "ok": True, "answer": "\n".join(lines),
                "evidence": {**evidence, "pushed": True}}

    async def _test(self, ctx: TaskContext, project: ProjectProfile, folder: Path
                    ) -> tuple[bool | None, str]:
        if not project.test_command:
            return None, ""
        await ctx.require("test.run", f"{folder} :: {project.test_command}")
        return await ctx.interruptible(run_tests(folder, project.test_command))

    async def _changed(self, folder: Path) -> bool:
        _, out = await git(folder, "status", "--porcelain")
        return any(ln.strip() and "__pycache__" not in ln for ln in out.splitlines())

    async def _report(self, lang: str, project: ProjectProfile, folder: Path, provider: str,
                      agent_text: str, passed: bool | None, test_out: str,
                      session: str | None, build_out: str = "") -> dict[str, Any]:
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
        if build_out.startswith("BUILD FAILED"):
            lines += ["", t["build_fail"].format(cmd=project.build_command),
                      _test_tail(build_out, 12)]
        elif project.build_command:
            lines += ["", t["build_ok"].format(cmd=project.build_command)]
        if build_out.startswith("BUILD FAILED"):
            pass
        elif passed is None:
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
