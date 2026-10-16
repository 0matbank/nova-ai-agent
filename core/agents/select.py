"""Specialist selection (plan §10 step 6–7, §49.1, §59, §61): from the Intent
Router's category + the request, pick the FEW agents that are needed, the kind of
AI work (→ provider) and the only skills they may use.

Deterministic rules, no AI call: this runs on every request and must be instant.
Runtime stays small however big the roster is: lead agent + project specialist
(when the request is about a project) + verifier, never more than
max_active_agents_per_task (§58 rule 1–2)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from core.agents.roster import Roster, find_project
from core.config.schema import AppConfig
from core.router.intent import Intent

# Coding sub-roles (§59): first match wins.
_SUB_ROLES: list[tuple[str, re.Pattern[str]]] = [
    ("security", re.compile(
        r"\b(security|vulnerab\w*|secrets?\s*(scan|leak|check)|leak(ed)?|audit)\b|"
        r"নিরাপত্তা|সিকিউরিটি", re.IGNORECASE)),
    ("devops", re.compile(
        r"\b(deploy\w*|release|ci|pipeline|docker\w*|github\s*actions?|workflow\s*file)\b",
        re.IGNORECASE)),
    ("android", re.compile(
        r"\b(android|apk|gradle|kotlin|jetpack|compose|viewmodel|workmanager)\b|অ্যান্ড্রয়েড",
        re.IGNORECASE)),
    ("frontend_ui", re.compile(
        r"\b(ui|ux|css|html|frontend|front-end|layout|design|button|theme|colou?r|font|"
        r"responsive|style)\b|ডিজাইন|বাটন|রং", re.IGNORECASE)),
    ("backend_architect", re.compile(
        r"\b(api|backend|back-end|database|server|schema|endpoint|architecture|sql|"
        r"migration)\b", re.IGNORECASE)),
]
# "Click TV test koro" (§13): run the project's own tests — no AI.
_TEST = re.compile(r"\b(tests?|unittest|pytest)\b|টেস্ট", re.IGNORECASE)
# Changing something is never a plain read: these send it to a coding agent.
_EDIT = re.compile(
    r"\b(fix|add|change\s*koro|implement|write|update|refactor|remove|rename|bug|lekho|"
    r"likho|banao|thik\s*koro|kor[oe]\s*dao)\b|ঠিক\s*কর|লিখ|যোগ\s*কর|বানাও|পরিবর্তন\s*কর",
    re.IGNORECASE)
# A read-only look at a repo's git state → the Git agent, no AI.
_GIT_READ = re.compile(
    r"\bgit\s*(status|diff|state|obostha)\b|\buncommitted\b|"
    r"\b(ki|what)\s*(ki\s*)?(change|changes|poriborton)\b|কী\s*কী?\s*পরিবর্তন|"
    r"\b(changes?|diff)\s*(dekhao|show|bolo)\b", re.IGNORECASE)

# category → (lead agent, route); routes are what the executor runs
_LEADS: dict[str, tuple[str, str]] = {
    "question": ("master_orchestrator", "answer"),
    "research": ("research", "answer"),
    "pc_status": ("windows_operator", "skill"),
    "screenshot": ("windows_operator", "skill"),
    "processes": ("windows_operator", "skill"),
    "pc_control": ("windows_operator", "operator"),
    "files": ("file_document", "operator"),
    "browser": ("browser", "browser"),
    "coding": ("developer", "coding"),
    "document": ("file_document", "not_yet"),
    "reminder": ("automation", "not_yet"),
    "social": ("master_orchestrator", "not_yet"),
    "communication": ("master_orchestrator", "not_yet"),
    "project": ("master_orchestrator", "project_info"),
}


@dataclass(frozen=True)
class Assignment:
    lead: str                         # agent that does the thinking
    agents: tuple[str, ...]           # active agents (lead first), incl. verifier
    route: str                        # answer | skill | operator | coding | git | browser |
                                      # project_test | project_info (an answer with the
                                      # project's context) | not_yet
    project: str | None
    provider_task_type: str | None
    skills: tuple[str, ...]           # allowlist: "skill" or "skill.tool"
    reason: str

    def allows(self, skill: str, tool: str) -> bool:
        return skill in self.skills or f"{skill}.{tool}" in self.skills

    def label(self, roster: Roster) -> str:
        return " + ".join(roster.title(a) for a in self.agents if a != "verifier")

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Assignment:
        return cls(str(d["lead"]), tuple(d["agents"]), str(d["route"]), d.get("project"),
                   d.get("provider_task_type"), tuple(d["skills"]), str(d.get("reason", "")))


def _coding_role(text: str, default: str) -> tuple[str, str]:
    # a security FIX is code work; a security LOOK went to the Security agent already
    for role, rx in _SUB_ROLES[1:]:
        m = rx.search(text)
        if m:
            return role, f"'{m.group(0)}' → {role}"
    return default, f"no sub-role keyword → {default}"


def assign(intent: Intent, text: str, cfg: AppConfig, roster: Roster) -> Assignment:
    category = intent.category
    lead, route = _LEADS.get(category, ("master_orchestrator", "answer"))
    reason = f"intent {category} ({intent.source})"
    # current-information questions name no project; everything else may be about one
    found = None if category in ("research",) else find_project(cfg, text)
    key = found[0] if found else None
    proj = roster.projects.get(key) if key else None
    edits = bool(_EDIT.search(text))
    security = _SUB_ROLES[0][1].search(text)

    if security and not edits and route in ("answer", "project_info", "coding", "operator"):
        # a security look is read-only, whatever the words around it (§50 Security Auditor)
        lead, route = "security", "operator"
        reason += f"; '{security.group(0)}' → security (read-only)"
    elif proj is not None and route in ("answer", "project_info", "coding"):
        if _GIT_READ.search(text) and not edits:
            lead, route, reason = "git", "git", reason + "; read-only git look"
        elif _TEST.search(text) and not edits:
            lead, route, reason = "project:" + proj.key, "project_test", \
                reason + "; run the project's tests"
        elif edits:
            route = "coding"
        else:
            # about a project but nothing to change ("git demo ki?" — the project's
            # name alone can look like code words): answer with its context
            lead, route = "master_orchestrator", "project_info"
    if route == "coding" and lead in ("developer", "master_orchestrator"):
        default = proj.profile.specialist_role if proj is not None else "developer"
        lead, why = _coding_role(text, default)
        reason += "; " + why

    agents: list[str] = [lead]
    if proj is not None and lead != "project:" + proj.key:
        agents.insert(0, "project:" + proj.key)      # project context first (§52.1, §59)
    agents.append("verifier")
    agents = list(dict.fromkeys(agents))[:roster.max_active]
    if "verifier" not in agents:                     # never complete without it (§30)
        agents[-1] = "verifier"
    lead_def = roster.agents.get(lead)
    skills = lead_def.skills if lead_def is not None else ()
    ptype = lead_def.provider_task_type if lead_def is not None else None
    if intent.skill is not None and route in ("skill", "operator") and lead != "security":
        # "notepad kholo", "pc status": a fixed skill call, no AI needed (§45, §49.1)
        route = "skill"
        reason += f"; deterministic {intent.skill[0]}.{intent.skill[1]}"
    if route in ("skill", "project_test", "git", "not_yet"):
        ptype = None
    if category == "screenshot" and intent.skill is None:
        ptype = "vision"                             # a question about the screen (§57)
    return Assignment(lead, tuple(agents), route, key, ptype, tuple(skills), reason)
