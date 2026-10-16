"""Agent roster: config/agents.yaml + the role prompts in agents/<name>/AGENT.md,
and the project specialists in agents/projects/<key>/ with their profile in
config/projects/<key>.yaml (plan §12, §13, §51).

Agents are provider-neutral (§8.11): an agent only names the kind of AI work
(provider_task_type) — which provider does it today is the provider router's
call. Nothing is loaded into an AI per agent; a role prompt is a few lines that
go into the one request that needs it (§58)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from core.config.schema import AppConfig, ProjectProfile

AGENTS_DIR = Path(__file__).resolve().parents[2] / "agents"
MAX_RULES_CHARS = 1500        # the Context Manager caps a section at 2000 (§49.2)


@dataclass(frozen=True)
class AgentDef:
    name: str
    title: str
    prompt: str
    provider_task_type: str | None
    skills: tuple[str, ...]
    task_types: tuple[str, ...]


@dataclass(frozen=True)
class ProjectAgent:
    key: str
    title: str
    prompt: str
    profile: ProjectProfile

    def rules(self, root: Path) -> str:
        """The specialist's knowledge + only THIS project's rules (§51: current project only)."""
        parts = [self.prompt]
        p = self.profile
        facts = [f"{label}: {value}" for label, value in (
            ("Local folder", p.local_folder), ("Repository", p.repository),
            ("Site", p.site_url), ("Build", p.build_command), ("Tests", p.test_command),
            ("Deploy", p.deploy_method)) if value]
        if p.approval_required_for:
            facts.append("Needs the owner's approval: " + ", ".join(p.approval_required_for))
        if facts:
            parts.append("\n".join(facts))
        if p.knowledge_dir:
            rules = root / p.knowledge_dir / "rules.md"
            if rules.is_file():
                parts.append(rules.read_text(encoding="utf-8", errors="replace").strip())
        return "\n\n".join(x for x in parts if x)[:MAX_RULES_CHARS]


def _read_prompt(folder: Path) -> tuple[str, str]:
    """(title, body) of an AGENT.md — the first '# ' line is the title."""
    f = folder / "AGENT.md"
    if not f.is_file():
        return "", ""
    lines = f.read_text(encoding="utf-8").strip().splitlines()
    title = lines[0].lstrip("# ").strip() if lines and lines[0].startswith("#") else ""
    body = "\n".join(lines[1:] if title else lines).strip()
    return title, body


class Roster:
    def __init__(self, agents: dict[str, AgentDef], projects: dict[str, ProjectAgent],
                 max_active: int) -> None:
        self.agents = agents
        self.projects = projects
        self.max_active = max_active

    @classmethod
    def load(cls, cfg: AppConfig, agents_dir: Path = AGENTS_DIR) -> Roster:
        agents = {}
        for name, entry in cfg.agents.agents.items():
            if not entry.enabled:
                continue
            title, prompt = _read_prompt(agents_dir / name)
            agents[name] = AgentDef(name, title or name.replace("_", " ").title(), prompt,
                                    entry.provider_task_type, tuple(entry.skills),
                                    tuple(entry.task_types))
        projects = {}
        for key in cfg.agents.project_agents:
            profile = cfg.projects.get(key)
            if profile is None:
                continue                         # listed, but no profile yet → inactive
            title, prompt = _read_prompt(agents_dir / "projects" / key)
            projects[key] = ProjectAgent(key, title or f"{profile.name} Specialist",
                                         prompt, profile)
        return cls(agents, projects, cfg.agents.runtime.max_active_agents_per_task)

    def title(self, name: str) -> str:
        if name.startswith("project:"):
            p = self.projects.get(name.split(":", 1)[1])
            return p.title if p else name
        a = self.agents.get(name)
        return a.title if a else name


def _names(key: str, p: ProjectProfile) -> set[str]:
    n = p.name.lower()
    return {x.lower() for x in (key, key.replace("-", " "), n, n.replace(" ", ""),
                                *p.aliases) if x}


def _hit(word: str, low: str) -> bool:
    if not word.isascii():
        # Bangla takes suffixes on the name itself: "ক্লিক টিভির", "ক্লিক টিভি-তে"
        return re.search(rf"(?<!\w){re.escape(word)}", low) is not None
    return re.search(rf"(?<![\w-]){re.escape(word)}(?![\w-])", low) is not None


def find_project(cfg: AppConfig, text: str, keywords: bool = True
                 ) -> tuple[str, ProjectProfile] | None:
    """The project a request is about: its name/alias first; else (optionally) a
    domain keyword that belongs to exactly one project (plan §49.1 'Click TV →
    Click TV Specialist')."""
    low = text.lower()
    for key, p in cfg.projects.items():
        if any(_hit(n, low) for n in _names(key, p)):
            return key, p
    if keywords:
        hits = [(k, p) for k, p in cfg.projects.items()
                if any(_hit(w.lower(), low) for w in p.keywords)]
        if len(hits) == 1:
            return hits[0]
    return None
