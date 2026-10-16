"""/schedules (Phase 19): the owner's scheduled jobs, and cancelling one."""

from __future__ import annotations

from zoneinfo import ZoneInfo

from channels.base import IncomingMessage
from core.orchestrator.schedule_route import describe
from core.scheduler import ScheduleStore


class ScheduleCommands:
    def __init__(self, store: ScheduleStore, timezone: str) -> None:
        self.store = store
        self.tz = ZoneInfo(timezone)

    async def schedules(self, msg: IncomingMessage, args: list[str]) -> str:
        if args and args[0].lower() in ("cancel", "stop", "bondho", "delete", "bad"):
            if len(args) < 2 or not args[1].lstrip("#").isdigit():
                return "ব্যবহার: /schedules cancel <id>"
            sid = int(args[1].lstrip("#"))
            return (f"🗑️ Schedule #{sid} বাতিল করলাম — আর চলবে না।" if self.store.cancel(sid)
                    else f"Schedule #{sid} পাওয়া যায়নি বা আগেই শেষ/বাতিল।")
        jobs = self.store.active()
        if not jobs:
            return ("⏰ কোনো schedule নেই। যেমন লিখুন: “protidin shokal 9 tay pc status bolo”")
        lines = [f"⏰ চালু schedule {len(jobs)}টা — বাতিল: /schedules cancel <id>"]
        for j in jobs:
            nxt = j.next_run_at.astimezone(self.tz) if j.next_run_at else None
            when = describe(j.kind, j.rule, j.condition, nxt, "bn")
            verb = "মনে করাব" if j.action == "remind" else "কাজ"
            src = " (config)" if (j.source or "").startswith("config:") else ""
            ran = f" · {j.run_count} বার চলেছে" if j.run_count else ""
            lines.append(f"#{j.id} {when}{src}\n   {verb}: {j.what[:120]}{ran}")
        return "\n".join(lines)
