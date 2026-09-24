"""Task commands (plan §27): free text → task, /tasks, /task, /cancel, /pause, /resume."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from channels.base import IncomingMessage, OutgoingMessage
from core.queue.engine import TaskEngine
from core.queue.states import TaskState
from core.queue.store import CancelOutcome, StepStatus, TaskStore, TaskView

USER_REQUEST = "user_request"

STATE_ICON = {
    TaskState.RECEIVED: "📥", TaskState.PLANNING: "🧭", TaskState.RUNNING: "⚙️",
    TaskState.WAITING_APPROVAL: "🔐", TaskState.VERIFYING: "🔎", TaskState.COMPLETED: "✅",
    TaskState.PAUSED: "⏸️", TaskState.FAILED: "❌", TaskState.CANCELLED: "🛑",
    TaskState.WAITING_DESKTOP: "🖥️", TaskState.WAITING_RESOURCE: "⏳",
    TaskState.RETRYING: "🔁",
}
STEP_LABEL = {StepStatus.DONE: "DONE", StepStatus.CURRENT: "CURRENT",
              StepStatus.FAILED: "FAILED", StepStatus.PENDING: ""}


def parse_task_id(args: list[str]) -> int | None:
    if not args:
        return None
    raw = args[0].lstrip("#")
    return int(raw) if raw.isdigit() else None


class TaskCommands:
    def __init__(self, store: TaskStore, engine: TaskEngine, list_limit: int,
                 timezone: str) -> None:
        self.store = store
        self.engine = engine
        self.list_limit = list_limit
        self.tz = ZoneInfo(timezone)

    def _time(self, dt: datetime | None) -> str:
        return dt.astimezone(self.tz).strftime("%d %b %H:%M") if dt else "-"

    # -------------------------------------------------------------- submit

    async def submit(self, msg: IncomingMessage) -> OutgoingMessage:
        title = " ".join(msg.text.split())[:60]
        task = self.store.create(title=title, request_text=msg.text, task_type=USER_REQUEST,
                                 channel=msg.channel, chat_id=msg.chat_id)
        self.engine.wake()
        lines = [f"📥 Task #{task.id} তৈরি হয়েছে — {task.state}"]
        if USER_REQUEST not in self.engine.executors:
            lines.append("AI planner এখনো চালু হয়নি (Phase 8) — তখন এই task নিজে থেকে চলবে।")
        if self.store.is_queue_paused():
            lines.append("⏸️ Queue এখন paused — /resume দিলে চলবে।")
        lines.append(f"অবস্থা: /task {task.id}   বাতিল: /cancel {task.id}")
        return OutgoingMessage("\n".join(lines))

    # ------------------------------------------------------------ commands

    async def tasks(self, msg: IncomingMessage, args: list[str]) -> str:
        rows = self.store.list_recent(self.list_limit)
        head = "📋 Tasks" + (" (queue ⏸️ PAUSED)" if self.store.is_queue_paused() else "")
        if not rows:
            return f"{head}\nএখনো কোনো task নেই।"
        lines = [head]
        for t in rows:
            lines.append(f"{STATE_ICON[t.state]} #{t.id} {t.state} — {t.title}")
        lines.append("\nবিস্তারিত: /task <id>")
        return "\n".join(lines)

    async def task(self, msg: IncomingMessage, args: list[str]) -> str:
        tid = parse_task_id(args)
        if tid is None:
            return "ব্যবহার: /task <id>   যেমন: /task 12"
        t = self.store.get(tid)
        if t is None:
            return f"Task #{tid} পাওয়া যায়নি।"
        return format_task(t, self._time)

    async def cancel(self, msg: IncomingMessage, args: list[str]) -> str:
        tid = parse_task_id(args)
        if tid is None:
            return "ব্যবহার: /cancel <id>   যেমন: /cancel 12"
        outcome = self.store.request_cancel(tid)
        return {
            CancelOutcome.CANCELLED: f"🛑 Task #{tid} cancel করা হয়েছে।",
            CancelOutcome.REQUESTED: f"🛑 Task #{tid} চলছে — পরের safe checkpoint-এ থামবে।",
            CancelOutcome.ALREADY_FINISHED: f"Task #{tid} আগেই শেষ হয়ে গেছে।",
            CancelOutcome.NOT_FOUND: f"Task #{tid} পাওয়া যায়নি।",
        }[outcome]

    async def pause(self, msg: IncomingMessage, args: list[str]) -> str:
        active = self.store.pause_queue()
        extra = (f"\nচলমান task {', '.join(f'#{i}' for i in active)} পরের checkpoint-এ থামবে।"
                 if active else "")
        return "⏸️ Queue paused — নতুন task শুরু হবে না।" + extra + "\nআবার চালু: /resume"

    async def resume(self, msg: IncomingMessage, args: list[str]) -> str:
        resumed = self.store.resume_queue()
        self.engine.wake()
        extra = (f"\nLast checkpoint থেকে আবার চলবে: {', '.join(f'#{i}' for i in resumed)}"
                 if resumed else "")
        return "▶️ Queue চালু হয়েছে।" + extra


def format_task(t: TaskView, fmt_time: Callable[[datetime | None], str]) -> str:
    lines = [
        f"{STATE_ICON[t.state]} TASK #{t.id} — {t.state}",
        f"Request: {t.request_text[:500]}",
        f"Created: {fmt_time(t.created_at)}",
    ]
    if t.finished_at:
        lines.append(f"Finished: {fmt_time(t.finished_at)}")
    if t.paused_from:
        lines.append(f"Paused from: {t.paused_from}")
    if t.retry_count:
        lines.append(f"Retries: {t.retry_count}")
    if t.cancel_requested:
        lines.append("Cancel requested — পরের checkpoint-এ থামবে")
    if t.steps:
        lines.append("")
        for s in t.steps:
            label = STEP_LABEL[s.status]
            lines.append(f"{s.seq}. {s.title}" + (f"  — {label}" if label else ""))
    if t.result_summary:
        lines.append(f"\nResult: {t.result_summary[:1000]}")
    if t.error_code:
        lines.append(f"\nError: {t.error_code}" + (f" — {t.error_message[:500]}"
                                                     if t.error_message else ""))
    return "\n".join(lines)
