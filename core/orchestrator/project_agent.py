"""Project specialist routes (plan §13, §51, §52; Phase 16) that need no coding AI:

- "Click TV test koro"      → the project's own test command, run by Nova (§13)
- "git demo-r git status"   → the Git agent's read-only look (git.status + git.diff)

Project context for AI answers/coding comes from ProjectAgent.rules(): the
specialist's own knowledge + only THIS project's profile and rules (§51)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.agents import Assignment, Roster
from core.orchestrator.coding import run_tests
from core.queue.engine import TaskContext
from core.skills.runner import SkillRunner

TEXT = {
    "bn": {
        "no_tests": "🧪 {name}: এই project-এ test command দেওয়া নেই (config/projects/{key}.yaml-এ "
                    "test_command লিখে দিন)।",
        "no_folder": "{name}: project-এর local folder এখনো config-এ দেওয়া নেই।",
        "tests_ok": "🧪 {name}: সব test পাস ✅ ({cmd})",
        "tests_fail": "🧪 {name}: test ফেল ❌ ({cmd})",
        "clean": "📊 {name}: branch {branch} — কোনো uncommitted পরিবর্তন নেই।",
        "changed": "📊 {name}: branch {branch} — {n}টা ফাইলে uncommitted পরিবর্তন:",
        "which": "কোন project? এগুলো set up করা আছে: {names}",
    },
    "en": {
        "no_tests": "🧪 {name}: this project has no test command (add test_command in "
                    "config/projects/{key}.yaml).",
        "no_folder": "{name}: the project's local folder is not in the config yet.",
        "tests_ok": "🧪 {name}: all tests passed ✅ ({cmd})",
        "tests_fail": "🧪 {name}: tests failed ❌ ({cmd})",
        "clean": "📊 {name}: branch {branch} — no uncommitted changes.",
        "changed": "📊 {name}: branch {branch} — uncommitted changes in {n} file(s):",
        "which": "Which project? Set up: {names}",
    },
}


def _tail(text: str, lines: int = 15) -> str:
    rows = [r for r in text.strip().splitlines() if r.strip()]
    return "\n".join(rows[-lines:])


def which_project(roster: Roster, lang: str) -> dict[str, Any]:
    names = ", ".join(p.profile.name for p in roster.projects.values()) or "—"
    return {"kind": "project", "ok": False, "needs_input": True,
            "answer": TEXT[lang]["which"].format(names=names)}


async def run_project_tests(ctx: TaskContext, roster: Roster, a: Assignment,
                            lang: str) -> dict[str, Any]:
    t = TEXT[lang]
    assert a.project is not None
    proj = roster.projects[a.project]
    p = proj.profile
    if not p.local_folder or not Path(p.local_folder).is_dir():
        return {"kind": "project", "ok": False, "needs_input": True,
                "answer": t["no_folder"].format(name=p.name)}
    if not p.test_command:
        return {"kind": "project", "ok": False, "needs_input": True,
                "answer": t["no_tests"].format(name=p.name, key=a.project)}
    folder = Path(p.local_folder).resolve()
    await ctx.require("test.run", f"{folder} :: {p.test_command}")
    passed, out = await ctx.interruptible(run_tests(folder, p.test_command))
    head = (t["tests_ok"] if passed else t["tests_fail"]).format(name=p.name,
                                                                 cmd=p.test_command)
    return {"kind": "project", "ok": True, "answer": f"{head}\n{_tail(out)}",
            "evidence": {"project": a.project, "tests_passed": passed,
                         "command": p.test_command}}


async def git_look(ctx: TaskContext, skills: SkillRunner, roster: Roster, a: Assignment,
                   lang: str) -> dict[str, Any]:
    t = TEXT[lang]
    assert a.project is not None
    name = roster.projects[a.project].profile.name
    st = await skills.invoke(ctx, "git", "status", {"project": a.project})
    if not st.ok:
        return {"kind": "skill", "skill": "git.status", "ok": False, "answer": st.summary,
                "evidence": st.evidence}
    changed = list(st.data.get("changed") or [])
    branch = st.data.get("branch", "?")
    if not changed:
        return {"kind": "skill", "skill": "git.status", "ok": True,
                "answer": t["clean"].format(name=name, branch=branch), "evidence": st.evidence}
    d = await skills.invoke(ctx, "git", "diff", {"project": a.project})
    lines = [t["changed"].format(name=name, branch=branch, n=len(changed)),
             *(f"• {f}" for f in changed[:20])]
    if d.ok and d.data.get("stat"):
        lines += ["", str(d.data["stat"])[-1500:]]
    return {"kind": "skill", "skill": "git.status", "ok": True, "answer": "\n".join(lines),
            "evidence": {**st.evidence, "diff": bool(d.ok)}}


def project_rules(root: Path, roster: Roster, a: Assignment | None) -> str:
    if a is None or a.project is None or a.project not in roster.projects:
        return ""
    return roster.projects[a.project].rules(root)
