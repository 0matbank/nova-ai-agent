"""Automation agent, Phase 19 (plan §39): turn "protidin shokal 9 tay … koro" into a
schedule in the DB and tell the owner the exact time it understood (so a wrong guess
is caught at once and can be cancelled with /schedules)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from core.queue.engine import TaskContext
from core.scheduler import ScheduleStore, parse_schedule

DAYS_BN = ("সোম", "মঙ্গল", "বুধ", "বৃহস্পতি", "শুক্র", "শনি", "রবি")
DAYS_EN = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

TEXT = {
    "bn": {
        "unclear": "⏰ কখন করব বুঝতে পারিনি। এভাবে লিখুন:\n• 30 minute por pc status bolo\n"
                   "• raat 2 tay scanner run koro\n• protidin shokal 9 tay website check koro\n"
                   "• proti shukrobar raat 10 tay backup koro\n• task #12 shesh hole amake janao",
        "past": "⏰ ওই সময়টা পেরিয়ে গেছে। ভবিষ্যতের একটা সময় দিন।",
        "nothing": "⏰ সময় বুঝেছি, কিন্তু কী করব বা কী মনে করাব সেটা পাইনি।",
        "saved": "⏰ Schedule #{id} রাখলাম — {when}\n{verb}: “{what}”\nবাতিল করতে: /schedules "
                 "cancel {id}",
        "task": "কাজ", "remind": "মনে করাব",
        "daily": "প্রতিদিন {at}", "weekly": "প্রতি {days} {at}",
        "every": "প্রতি {n} মিনিটে (প্রথম: {first})", "at": "{first}",
        "cond_task": "Task #{n} শেষ হলেই", "cond_file": "{path} এলেই",
    },
    "en": {
        "unclear": "⏰ I couldn't tell when. Try:\n• in 30 minutes check pc status\n"
                   "• at 2 am run the scanner\n• every day at 9 am check the website\n"
                   "• every friday at 10 pm run the backup\n• when task #12 is done tell me",
        "past": "⏰ That time has already passed — please give a future time.",
        "nothing": "⏰ I got the time, but not what to do or remind you of.",
        "saved": "⏰ Schedule #{id} saved — {when}\n{verb}: “{what}”\nTo cancel: /schedules "
                 "cancel {id}",
        "task": "Task", "remind": "Reminder",
        "daily": "every day at {at}", "weekly": "every {days} at {at}",
        "every": "every {n} min (first: {first})", "at": "{first}",
        "cond_task": "as soon as task #{n} finishes", "cond_file": "as soon as {path} appears",
    },
}


def describe(kind: str, rule: dict[str, Any] | None, condition: dict[str, Any] | None,
             first: datetime | None, lang: str) -> str:
    t = TEXT[lang]
    days = DAYS_BN if lang == "bn" else DAYS_EN
    first_s = first.strftime("%d %b %Y, %I:%M %p") if first else ""
    if condition:
        if condition.get("type") == "task_finished":
            return str(t["cond_task"]).format(n=condition.get("task_id"))
        return str(t["cond_file"]).format(path=condition.get("path"))
    if rule and "every_minutes" in rule:
        return str(t["every"]).format(n=rule["every_minutes"], first=first_s)
    if rule and rule.get("every") == "day":
        return str(t["daily"]).format(at=_ampm(str(rule["at"])))
    if rule and rule.get("every") == "week":
        names = ", ".join(days[d] for d in rule.get("days") or [])
        return str(t["weekly"]).format(days=names, at=_ampm(str(rule["at"])))
    return str(t["at"]).format(first=first_s)


def _ampm(hhmm: str) -> str:
    return datetime.strptime(hhmm, "%H:%M").strftime("%I:%M %p")


def schedule_request(ctx: TaskContext, store: ScheduleStore, timezone: str,
                     lang: str) -> dict[str, Any]:
    t = TEXT[lang]
    now = datetime.now(ZoneInfo(timezone))
    spec = parse_schedule(ctx.task.request_text, now)
    if spec is None:
        return {"kind": "schedule", "ok": False, "needs_input": True, "answer": t["unclear"]}
    if spec.notes and spec.first_run is None and spec.kind != "condition":
        return {"kind": "schedule", "ok": False, "needs_input": True, "answer": t["past"]}
    if len(spec.what) < 2 and spec.kind == "condition" and spec.action == "remind":
        # "task #12 shesh hole amake janao": the condition itself is the news
        spec.what = "আপনি এটা জানাতে বলেছিলেন" if lang == "bn" else "you asked to be told"
    if len(spec.what) < 2:
        return {"kind": "schedule", "ok": False, "needs_input": True, "answer": t["nothing"]}
    first_utc = spec.first_run.astimezone(UTC) if spec.first_run else None
    job = store.add(kind=spec.kind, action=spec.action, what=spec.what,
                    next_run_at=first_utc, channel=ctx.task.channel, chat_id=ctx.task.chat_id,
                    rule=spec.rule, condition=spec.condition,
                    request_text=ctx.task.request_text)
    when = describe(spec.kind, spec.rule, spec.condition, spec.first_run, lang)
    return {"kind": "schedule", "ok": True,
            "answer": str(t["saved"]).format(id=job.id, when=when, verb=t[spec.action],
                                             what=spec.what),
            "evidence": {"schedule_id": job.id, "schedule_kind": spec.kind,
                         "next_run_at": first_utc.isoformat() if first_utc else None}}
