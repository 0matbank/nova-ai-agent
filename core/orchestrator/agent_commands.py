"""/agents (plan §27) and /memory (Phase 17): the specialist roster and what Nova
keeps about each project. Deterministic, read-only except `/memory <p> forget <id>`."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from channels.base import IncomingMessage
from core.agents import Roster, find_project
from core.config.schema import AppConfig
from core.memory import ProjectMemory

KIND_BN = {"decision": "📌 সিদ্ধান্ত", "fact": "📝 তথ্য", "known_issue": "⚠️ খোলা সমস্যা",
           "task": "✅ আগের কাজ", "finding": "🔎 review"}


class AgentCommands:
    def __init__(self, cfg: AppConfig, roster: Roster, memory: ProjectMemory | None,
                 timezone: str) -> None:
        self.cfg = cfg
        self.roster = roster
        self.memory = memory
        self.tz = ZoneInfo(timezone)

    def _time(self, ts: datetime) -> str:
        return ts.astimezone(self.tz).strftime("%d %b %H:%M")

    async def agents(self, msg: IncomingMessage, args: list[str]) -> str:
        lines = [f"🧭 Specialist agents — একটা কাজে সর্বোচ্চ "
                 f"{self.roster.max_active}টা (verifier সহ)", ""]
        for a in self.roster.agents.values():
            ai = f"AI: {a.provider_task_type}" if a.provider_task_type else "AI ছাড়া"
            skills = ", ".join(a.skills) if a.skills else "—"
            lines.append(f"• {a.title} — {ai}; skills: {skills}")
        lines += ["", "📁 Project specialists"]
        for key in self.cfg.agents.project_agents:
            p = self.roster.projects.get(key)
            if p is None:
                lines.append(f"• {key} — profile নেই (config/projects/{key}.yaml যোগ করলে চালু)")
                continue
            ready = "✅" if p.profile.local_folder else "⚠️ folder দেওয়া নেই"
            lines.append(f"• {p.title} ({key}) {ready}")
        return "\n".join(lines)

    async def memory_cmd(self, msg: IncomingMessage, args: list[str]) -> str:
        if self.memory is None:
            return "🧠 Project memory চালু নেই (database চালু হয়নি)।"
        if not args:
            lines = ["🧠 Project memory — দেখতে: /memory <project>"]
            for key, p in self.roster.projects.items():
                n = len(self.memory.entries(key))
                lines.append(f"• {p.profile.name} ({key}): {n}টা")
            return "\n".join(lines)
        # "/memory git demo forget 7": the longest leading words that name a project
        found, rest = None, args
        for n in range(len(args), 0, -1):
            found = self._project(" ".join(args[:n]))
            if found is not None:
                rest = args[n:]
                break
        if found is None:
            names = ", ".join(self.roster.projects) or "—"
            return f"❓ '{' '.join(args)}' নামে project নেই। আছে: {names}"
        key = found
        args = [key, *rest]
        if len(args) >= 3 and args[1].lower() in ("forget", "delete", "muche", "bad"):
            if not args[2].lstrip("#").isdigit():
                return "ব্যবহার: /memory <project> forget <id>"
            ok = self.memory.forget(key, int(args[2].lstrip("#")))
            return f"🗑️ #{args[2].lstrip('#')} মুছে দিলাম।" if ok else "সেই id পাওয়া যায়নি।"
        entries = self.memory.entries(key, limit=30)
        name = self.roster.projects[key].profile.name
        if not entries:
            return (f"🧠 {name}: এখনো কিছু মনে রাখা নেই।\nরাখতে: “{name}-te mone rakho: …”")
        lines = [f"🧠 {name} — {len(entries)}টা (নতুন আগে); মুছতে: /memory {key} forget <id>"]
        for e in entries:
            lines.append(f"#{e.id} {KIND_BN.get(e.kind, e.kind)} · {self._time(e.updated_at)}\n"
                         f"   {e.content[:220]}")
        return "\n".join(lines)

    def _project(self, words: str) -> str | None:
        """The project these words ARE (whole phrase), not one they merely contain."""
        words = " ".join(words.strip().lower().split())
        hit = find_project(self.cfg, words, keywords=False)
        if hit is None or hit[0] not in self.roster.projects:
            return None
        key, p = hit
        names = {key, key.replace("-", " "), p.name.lower(), p.name.lower().replace(" ", ""),
                 *(a.lower() for a in p.aliases)}
        return key if words in names else None
