"""Project specialist routes (plan §13, §51, §52; Phase 16) that need no coding AI:

- "Click TV test koro"      → the project's own test command, run by Nova (§13)
- "git demo-r git status"   → the Git agent's read-only look (git.status + git.diff)
- "Click TV-te mone rakho: …" → the project's memory (Phase 17)

Phase 17: after a project task its outcome goes into that project's memory
(record_outcome), and every later task on the project gets it back
(memory_context) — "project context persists between tasks".

Project context for AI answers/coding comes from ProjectAgent.rules(): the
specialist's own knowledge + only THIS project's profile and rules (§51)."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from core.agents import Assignment, Roster
from core.memory import ProjectMemory, SecretRefused
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


# ------------------------------------------------------------ Phase 17 memory

_NOTE_TRIGGER = re.compile(
    r"\b(please\s+)?(mone\s*rakh(o|ben|un|e\s*dao)?|mone\s*rekho|remember\s+(that|this)|"
    r"note\s*(koro|kore\s*rakho))\b|মনে\s*রাখ\S*|মনে\s*রেখ\S*|নোট\s*কর\S*", re.IGNORECASE)
_DECISION = re.compile(r"\b(always|never|must|decid\w*|rule|sobsomoy|kokhono\s*na|"
                       r"kokhonoi\s*na)\b|সবসময়|কখনো\s*না|সিদ্ধান্ত|নিয়ম", re.IGNORECASE)
_ERROR_LINE = re.compile(r"Error|Exception|FAIL|assert|Traceback", re.IGNORECASE)

MEM_TEXT: dict[str, dict[str, Any]] = {
    "bn": {"saved": "🧠 {name}-এর memory-তে রাখলাম ({kind}, #{id}):\n“{text}”\n"
                    "দেখতে: /memory {key}",
           "secret": "🔒 এতে password/token/key-এর মতো কিছু আছে — এগুলো memory-তে রাখি না "
                     "(secrets vault-এ থাকবে)। গোপন অংশ বাদ দিয়ে আবার পাঠান।",
           "empty": "কী মনে রাখব? যেমন: “{name}-te mone rakho: player HLS.js use kore”",
           "kinds": {"decision": "সিদ্ধান্ত", "fact": "তথ্য"}},
    "en": {"saved": "🧠 Saved to {name}'s memory ({kind}, #{id}):\n“{text}”\nSee: /memory {key}",
           "secret": "🔒 That contains something like a password/token/key — those never go "
                     "into memory (they belong in the secrets vault). Send it without that.",
           "empty": "What should I remember? e.g. “{name}: remember that the player uses HLS.js”",
           "kinds": {"decision": "decision", "fact": "fact"}},
}


def memory_context(memory: ProjectMemory | None, a: Assignment | None) -> tuple[str, str]:
    """(relevant memory, previous decisions) of THIS project only (§49.2, §58 rule 13)."""
    if memory is None or a is None or a.project is None:
        return "", ""
    return memory.context(a.project)


def remember_note(ctx: TaskContext, memory: ProjectMemory, roster: Roster, a: Assignment,
                  lang: str) -> dict[str, Any]:
    """"Click TV-te mone rakho: …" → a decision / fact in that project's memory."""
    assert a.project is not None
    t = MEM_TEXT[lang]
    p = roster.projects[a.project].profile
    text = ctx.task.request_text
    m = _NOTE_TRIGGER.search(text)
    note = text[m.end():] if m else text
    note = re.sub(r"^[\s:,\-–—।]+", "", note).strip()
    if len(note) < 3 and m:                       # "…, eta mone rakho" — the note came first
        note = text[:m.start()]
        for n in sorted({p.name, a.project, *p.aliases}, key=len, reverse=True):
            note = re.sub(re.escape(n) + r"\S*", "", note, flags=re.IGNORECASE)
        note = re.sub(r"^[\s:,\-–—।]+|[\s:,\-–—।]+$", "", note).strip()
    if len(note) < 3:
        return {"kind": "project", "ok": False, "needs_input": True,
                "answer": t["empty"].format(name=p.name)}
    kind = "decision" if _DECISION.search(note) else "fact"
    try:
        entry = memory.put(a.project, kind, note, source="owner", task_id=ctx.task.id,
                           project_name=p.name)
    except SecretRefused:
        return {"kind": "project", "ok": False, "needs_input": True, "answer": t["secret"]}
    return {"kind": "project", "ok": True,
            "answer": t["saved"].format(name=p.name, kind=t["kinds"][kind], id=entry,
                                        text=note[:300], key=a.project),
            "evidence": {"project": a.project, "memory_id": entry, "memory_kind": kind}}


def record_outcome(memory: ProjectMemory | None, roster: Roster, a: Assignment | None,
                   task_id: int, goal: str, out: dict[str, Any]) -> None:
    """After a project task: what happened goes into that project's memory, by rules
    (no AI, plan §21: only project facts, never temporary chatter or secrets)."""
    if memory is None or a is None or a.project is None or a.project not in roster.projects:
        return
    if not out.get("ok") or out.get("needs_input"):
        return
    key, name = a.project, roster.projects[a.project].profile.name
    ev = out.get("evidence") or {}
    day = datetime.now().strftime("%Y-%m-%d")
    goal = " ".join(goal.split())[:160]
    kind = out.get("kind")
    if kind == "project" and "tests_passed" in ev:
        cmd = ev.get("command")
        if ev.get("tests_passed"):
            memory.resolve(key, "known_issue", "tests")
            memory.put(key, "task", f"Task #{task_id} ({day}): tests passed ({cmd}).",
                       key=f"task:{task_id}", source="nova", task_id=task_id,
                       project_name=name)
        else:
            errors = [ln.strip() for ln in str(out.get("answer", "")).splitlines()
                      if _ERROR_LINE.search(ln)][-3:]
            memory.put(key, "known_issue", f"Tests fail ({cmd}) since task #{task_id} ({day}): "
                       + (" | ".join(errors) or "see the test output"),
                       key="tests", source="nova", task_id=task_id, project_name=name)
    elif kind == "coding":
        files = ", ".join((ev.get("files_changed") or [])[:8]) or "no files"
        tests = {True: "tests passed", False: "tests FAILED", None: "no test command"}[
            ev.get("tests_passed") if ev.get("tests_passed") in (True, False) else None]
        where = ""
        if ev.get("branch"):
            where = f"; committed on {ev['branch']}" + (" and pushed" if ev.get("pushed")
                                                         else " (not pushed)")
        memory.put(key, "task", f"Task #{task_id} ({day}) by {ev.get('provider')}: "
                   f"\"{goal}\" → changed {files}; {tests}{where}.",
                   key=f"task:{task_id}", source="nova", task_id=task_id, project_name=name)
        if ev.get("tests_passed") is True:
            memory.resolve(key, "known_issue", "tests")
    elif kind == "operator" and a.lead == "security":
        memory.put(key, "finding", f"Task #{task_id} ({day}) security look: "
                   f"{str(out.get('answer', ''))[:500]}",
                   key=f"finding:{task_id}", source="nova", task_id=task_id, project_name=name)
