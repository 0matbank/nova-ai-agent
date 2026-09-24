"""/skills (plan §27): loaded skills, their tools and permission levels."""

from __future__ import annotations

from channels.base import IncomingMessage
from core.permissions.engine import PermissionEngine
from core.skills.registry import SkillRegistry

LEVEL_ICON = {"GREEN": "🟢", "BLUE": "🔵", "YELLOW": "🟡", "RED": "🔴"}


class SkillCommands:
    def __init__(self, registry: SkillRegistry, permissions: PermissionEngine | None) -> None:
        self.registry = registry
        self.permissions = permissions

    def _levels(self, actions: list[str]) -> str:
        if self.permissions is None:
            return ""
        levels = sorted({self.permissions.level_of(a).value for a in actions},
                        key=["GREEN", "BLUE", "YELLOW", "RED"].index)
        return "".join(LEVEL_ICON[lv] for lv in levels)

    async def skills(self, msg: IncomingMessage, args: list[str]) -> str:
        lines = [f"🧰 Skills ({len(self.registry.skills)})"]
        for name, sk in sorted(self.registry.skills.items()):
            lines.append(f"\n• {name} v{sk.manifest.version} — {sk.manifest.description}")
            tools = [f"{t} {self._levels(sk.declared.get(t, []))}" for t in sk.tools]
            lines.append("  " + ", ".join(tools))
        lines.append("\n🟢 auto  🔵 auto+log  🟡 safety check  🔴 approval")
        return "\n".join(lines)
