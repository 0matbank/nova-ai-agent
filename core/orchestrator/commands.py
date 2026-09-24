"""Deterministic command handling (plan §27). Commands never go through an LLM
(§45: "Every command through LLM" is explicitly not built)."""

from __future__ import annotations

import difflib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from channels.base import IncomingMessage, OutgoingMessage
from core.health import format_duration, format_pc_status, pc_status

# Every command from plan §27. Ones without a handler yet reply "not yet".
ALL_COMMANDS = (
    "/status", "/tasks", "/task", "/cancel", "/pause", "/resume", "/screenshot",
    "/pc", "/skills", "/agents", "/logs", "/restart-agent", "/update", "/lockdown", "/help",
)

COMMAND_DESCRIPTIONS = {
    "/status": "Agent health + PC status",
    "/pc": "CPU / GPU / RAM / Disk",
    "/help": "সব command-এর তালিকা",
}

TEXT_ACK = (
    "✅ বার্তা পেয়েছি।\n"
    "Task engine এখনো চালু হয়নি (Phase 3) — তখন এটা task হিসেবে চলবে।\n"
    "এখন ব্যবহার করা যাবে: /status, /pc, /help"
)


@dataclass
class HealthSources:
    """Pluggable health probes; each returns (ok, detail)."""
    probes: dict[str, Callable[[], tuple[bool, str]]] = field(default_factory=dict)


Handler = Callable[[IncomingMessage, list[str]], Awaitable[str]]


class CommandRouter:
    def __init__(self, disk_path: Path, health: HealthSources, agent_name: str) -> None:
        self.started_at = time.time()
        self.disk_path = disk_path
        self.health = health
        self.agent_name = agent_name
        self._handlers: dict[str, Handler] = {
            "/status": self._status,
            "/pc": self._pc,
            "/help": self._help,
        }

    async def handle(self, msg: IncomingMessage) -> OutgoingMessage | None:
        if not msg.is_command:
            return OutgoingMessage(TEXT_ACK)
        parts = msg.text.split()
        # "/status@MyBot" → "/status"
        command = parts[0].split("@", 1)[0].lower()
        handler = self._handlers.get(command)
        if handler:
            return OutgoingMessage(await handler(msg, parts[1:]))
        if command in ALL_COMMANDS:
            return OutgoingMessage(f"⏳ {command} এখনো চালু হয়নি — পরের phase-এ আসবে।")
        guess = difflib.get_close_matches(command, ALL_COMMANDS, n=1, cutoff=0.7)
        hint = f"আপনি কি {guess[0]} বোঝাতে চেয়েছেন?\n" if guess else ""
        return OutgoingMessage(f"❓ অজানা command: {command}\n{hint}/help দেখুন।")

    def menu(self) -> list[tuple[str, str]]:
        """(command, description) for Telegram's "/" menu — only working commands."""
        return [(c, COMMAND_DESCRIPTIONS[c]) for c in ALL_COMMANDS if c in self._handlers]

    async def _status(self, msg: IncomingMessage, args: list[str]) -> str:
        lines = [f"🤖 {self.agent_name} — online",
                 f"Agent uptime: {format_duration(time.time() - self.started_at)}"]
        all_ok = True
        for name, probe in self.health.probes.items():
            try:
                ok, detail = probe()
            except Exception as e:  # a broken probe must not break /status
                ok, detail = False, f"probe error: {type(e).__name__}"
            all_ok &= ok
            lines.append(f"{'✅' if ok else '❌'} {name}: {detail}")
        lines.insert(1, f"Health: {'OK' if all_ok else 'DEGRADED'}")
        lines.append("")
        lines.append(format_pc_status(await pc_status(self.disk_path)))
        return "\n".join(lines)

    async def _pc(self, msg: IncomingMessage, args: list[str]) -> str:
        return "🖥️ PC status\n" + format_pc_status(await pc_status(self.disk_path))

    async def _help(self, msg: IncomingMessage, args: list[str]) -> str:
        ready = [c for c in ALL_COMMANDS if c in self._handlers]
        pending = [c for c in ALL_COMMANDS if c not in self._handlers]
        return (
            "📖 Commands\n"
            f"চালু: {' '.join(ready)}\n"
            f"আসছে: {' '.join(pending)}\n\n"
            "সাধারণ বাংলা/English/Banglish text-ও পাঠাতে পারবেন।"
        )
