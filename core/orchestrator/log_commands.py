"""/logs (plan §27, §42 "Activity logs"): the recent audit trail on the phone —
what Nova did, what was approved or refused, provider sign-in problems.
Read-only; texts are already redacted when they are written."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from channels.base import IncomingMessage
from core.db.models import AuditLog
from core.log import redact

DEFAULT, MAX = 15, 50
ICON = {"ok": "✅", "pending": "⏳", "approved": "👍", "rejected": "🚫", "denied": "🚫",
        "failed": "❌", "error": "❌", "expired": "⌛", "used": "✔️"}


class LogCommands:
    def __init__(self, sessions: sessionmaker[Session], timezone: str) -> None:
        self.sessions = sessions
        self.tz = ZoneInfo(timezone)

    def _time(self, ts: datetime) -> str:
        return ts.astimezone(self.tz).strftime("%d %b %H:%M")

    async def logs(self, msg: IncomingMessage, args: list[str]) -> str:
        n = DEFAULT
        if args and args[0].isdigit():
            n = max(1, min(int(args[0]), MAX))
        with self.sessions() as s:
            rows = list(s.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(n)))
        if not rows:
            return "📜 এখনো কোনো activity log নেই।"
        lines = [f"📜 শেষ {len(rows)}টা কাজ (নতুন আগে) — বেশি দেখতে: /logs 30"]
        for r in rows:
            icon = ICON.get(r.status.lower(), "•")
            task = f" #{r.task_id}" if r.task_id else ""
            target = f" — {redact(r.target)[:60]}" if r.target else ""
            lines.append(f"{icon} {self._time(r.ts)}{task} {r.action} [{r.status}]{target}")
        return "\n".join(lines)
