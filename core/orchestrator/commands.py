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
from core.orchestrator.agent_commands import AgentCommands
from core.orchestrator.log_commands import LogCommands
from core.orchestrator.schedule_commands import ScheduleCommands
from core.orchestrator.screen_commands import ScreenCommands
from core.orchestrator.security_commands import SecurityCommands
from core.orchestrator.skill_commands import SkillCommands
from core.orchestrator.task_commands import TaskCommands

# Every command from plan §27. Ones without a handler yet reply "not yet".
ALL_COMMANDS = (
    "/status", "/tasks", "/task", "/cancel", "/pause", "/resume", "/screenshot",
    "/pc", "/skills", "/agents", "/logs", "/restart-agent", "/update", "/lockdown", "/help",
    "/memory",                      # Phase 17 addition: what Nova keeps about each project
    "/schedules",                   # Phase 19 addition: scheduled jobs (plan §39)
)

COMMAND_DESCRIPTIONS = {
    "/status": "Agent health + PC status",
    "/tasks": "সাম্প্রতিক task-এর তালিকা",
    "/task": "একটি task-এর বিস্তারিত: /task <id>",
    "/cancel": "Task বাতিল: /cancel <id>",
    "/pause": "Queue pause",
    "/resume": "Queue আবার চালু",
    "/screenshot": "PC-র screen-এর ছবি",
    "/pc": "CPU / GPU / RAM / Disk",
    "/skills": "Skill ও tool-এর তালিকা (permission level সহ)",
    "/logs": "সাম্প্রতিক কাজের log (audit): /logs বা /logs 30",
    "/agents": "Specialist ও project agent-দের তালিকা",
    "/memory": "Project memory: /memory <project> (মুছতে: forget <id>)",
    "/schedules": "Schedule-এর তালিকা (বাতিল: /schedules cancel <id>)",
    "/lockdown": "Emergency: সব dangerous কাজ বন্ধ (off দিলে খোলে)",
    "/restart-agent": "Nova restart (চলমান কাজ checkpoint থেকে আবার চলবে)",
    "/update": "নতুন version install (approval লাগবে; ব্যর্থ হলে নিজে আগের version-এ ফেরে)",
    "/help": "সব command-এর তালিকা",
}

TASK_COMMANDS = ("/tasks", "/task", "/cancel", "/pause", "/resume", "/lockdown")

SAFE_MODE_REPLY = (
    "⚠️ SAFE MODE — database চালু হয়নি, তাই task নেওয়া/চালানো বন্ধ।\n"
    "কারণ: {reason}\n/status দেখুন।"
)


@dataclass
class HealthSources:
    """Pluggable health probes; each returns (ok, detail)."""
    probes: dict[str, Callable[[], tuple[bool, str]]] = field(default_factory=dict)


Handler = Callable[[IncomingMessage, list[str]], Awaitable[str | OutgoingMessage]]


class CommandRouter:
    def __init__(self, disk_path: Path, health: HealthSources, agent_name: str,
                 tasks: TaskCommands | None = None,
                 safe_mode_reason: str | None = None,
                 security: SecurityCommands | None = None,
                 skills: SkillCommands | None = None,
                 screen: ScreenCommands | None = None,
                 logs: LogCommands | None = None,
                 agents: AgentCommands | None = None,
                 schedules: ScheduleCommands | None = None) -> None:
        self.started_at = time.time()
        self.disk_path = disk_path
        self.health = health
        self.agent_name = agent_name
        self.tasks = tasks
        self.safe_mode_reason = safe_mode_reason
        self._handlers: dict[str, Handler] = {
            "/status": self._status,
            "/pc": self._pc,
            "/help": self._help,
        }
        if tasks is not None:
            self._handlers.update({
                "/tasks": tasks.tasks, "/task": tasks.task, "/cancel": tasks.cancel,
                "/pause": tasks.pause, "/resume": tasks.resume,
            })
        if security is not None:
            self._handlers["/lockdown"] = security.lockdown
        if skills is not None:
            self._handlers["/skills"] = skills.skills
        if screen is not None:
            self._handlers["/screenshot"] = screen.screenshot
        if logs is not None:
            self._handlers["/logs"] = logs.logs
        if agents is not None:
            self._handlers["/agents"] = agents.agents
            self._handlers["/memory"] = agents.memory_cmd
        if schedules is not None:
            self._handlers["/schedules"] = schedules.schedules

    def add(self, command: str, handler: Handler) -> None:
        assert command in ALL_COMMANDS, command
        self._handlers[command] = handler

    def _safe_mode(self) -> OutgoingMessage:
        return OutgoingMessage(SAFE_MODE_REPLY.format(reason=self.safe_mode_reason or "-"))

    async def handle(self, msg: IncomingMessage) -> OutgoingMessage | None:
        if not msg.is_command:
            if self.tasks is None:
                return self._safe_mode()
            return await self.tasks.submit(msg)
        parts = msg.text.split()
        # "/status@MyBot" → "/status"
        command = parts[0].split("@", 1)[0].lower()
        if command not in ALL_COMMANDS and command.replace("_", "-") in ALL_COMMANDS:
            command = command.replace("_", "-")     # Telegram's menu: /restart_agent
        if command in TASK_COMMANDS and self.tasks is None:
            return self._safe_mode()
        handler = self._handlers.get(command)
        if handler:
            result = await handler(msg, parts[1:])
            return result if isinstance(result, OutgoingMessage) else OutgoingMessage(result)
        if command in ALL_COMMANDS:
            return OutgoingMessage(f"⏳ {command} এখনো চালু হয়নি — পরের phase-এ আসবে।")
        guess = difflib.get_close_matches(command, ALL_COMMANDS, n=1, cutoff=0.7)
        hint = f"আপনি কি {guess[0]} বোঝাতে চেয়েছেন?\n" if guess else ""
        return OutgoingMessage(f"❓ অজানা command: {command}\n{hint}/help দেখুন।")

    def menu(self) -> list[tuple[str, str]]:
        """(command, description) for Telegram's "/" menu — only working commands.
        Telegram allows only a-z, 0-9 and _ there, so /restart-agent is /restart_agent."""
        return [(c.replace("-", "_"), COMMAND_DESCRIPTIONS[c]) for c in ALL_COMMANDS
                if c in self._handlers]

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
        mode = "SAFE MODE" if self.safe_mode_reason else ("OK" if all_ok else "DEGRADED")
        lines.insert(1, f"Health: {mode}")
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
