"""Understand a scheduling request (plan §39) — Bangla, Banglish or English, by rules
(no AI: a wrong time would be worse than asking again). The owner always gets the
exact time back to check.

  "30 minute por download status bolo"            delayed   → task
  "raat 2 tay scanner run koro"                   once      → task   (next 02:00)
  "kal shokal 9 tay amake mone koriye dio meeting" once     → remind
  "protidin shokal 9 tay website health check koro" recurring (daily 09:00) → task
  "proti shukrobar raat 10 tay backup koro"       recurring (weekly Fri 22:00)
  "proti 2 ghonta por por pc status bolo"         recurring (every 120 min)
  "task #12 shesh hole amake janao"               condition (task finished) → remind
  "D:\\x\\report.pdf file ashle amake bolo"       condition (file exists)   → remind
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")

UNIT_MINUTES = {"second": 1 / 60, "sec": 1 / 60, "সেকেন্ড": 1 / 60,
                "minute": 1, "minutes": 1, "min": 1, "mins": 1, "minit": 1, "mint": 1,
                "মিনিট": 1, "ghonta": 60, "ghonte": 60, "ghanta": 60, "hour": 60, "hours": 60,
                "hr": 60, "hrs": 60, "ঘণ্টা": 60, "ঘন্টা": 60, "din": 1440, "day": 1440,
                "days": 1440, "দিন": 1440}
_UNIT = "|".join(sorted(map(re.escape, UNIT_MINUTES), key=len, reverse=True))
DELAY = re.compile(rf"(\d+(?:\.\d+)?)\s*({_UNIT})\s*(?:por|pore|পর|পরে|later|after|e)\b|"
                   rf"\b(?:in|after)\s+(\d+(?:\.\d+)?)\s*({_UNIT})\b", re.IGNORECASE)
EVERY_N = re.compile(rf"(?:proti|prottek|every|প্রতি)\s*(\d+)\s*({_UNIT})"
                     r"(?:\s*(?:por\s*por|পর\s*পর|পরপর))?", re.IGNORECASE)
DAILY = re.compile(r"\b(protidin|proti\s*din|prottek\s*din|every\s*day|everyday|daily|roj|"
                   r"rooj)\b|প্রতিদিন|প্রতি\s*দিন|রোজ", re.IGNORECASE)
WEEKDAYS: dict[int, tuple[str, ...]] = {
    0: ("shombar", "sombar", "somobar", "shomobar", "monday", "mon", "সোমবার"),
    1: ("mongolbar", "mongalbar", "tuesday", "tue", "মঙ্গলবার"),
    2: ("budhbar", "budbar", "wednesday", "wed", "বুধবার"),
    3: ("brihospotibar", "brihoshpotibar", "bishudbar", "thursday", "thu", "বৃহস্পতিবার"),
    4: ("shukrobar", "sukrobar", "shukkurbar", "friday", "fri", "শুক্রবার"),
    5: ("shonibar", "sonibar", "saturday", "sat", "শনিবার"),
    6: ("robibar", "rabibar", "sunday", "sun", "রবিবার"),
}
_DAY_WORDS = {w: d for d, ws in WEEKDAYS.items() for w in ws}
WEEKLY = re.compile(r"(?:proti|prottek|every|প্রতি)\s*(" + "|".join(
    sorted(map(re.escape, _DAY_WORDS), key=len, reverse=True)) + r")", re.IGNORECASE)
PERIODS = {"bhor": "early", "ভোর": "early", "shokal": "am", "sokal": "am", "সকাল": "am",
           "morning": "am", "dupur": "noon", "দুপুর": "noon", "noon": "noon",
           "bikel": "pm", "bikal": "pm", "বিকেল": "pm", "বিকাল": "pm", "afternoon": "pm",
           "shondha": "pm", "sondha": "pm", "sandhya": "pm", "সন্ধ্যা": "pm", "evening": "pm",
           "raat": "night", "rat": "night", "রাত": "night", "night": "night", "tonight": "night"}
PERIOD = re.compile(r"(" + "|".join(sorted(map(re.escape, PERIODS), key=len, reverse=True))
                    + r")(?:e|ে|er|ের)?", re.IGNORECASE)
CLOCK = re.compile(r"(?<![\d:])(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.|ta|tay|tai|"
                   r"te|টায়|টা|টার|o'?clock|baje|বাজে)(?![a-z])|(?<![\d:])([01]?\d|2[0-3]):"
                   r"([0-5]\d)(?![\d:])|\bat\s+(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?\b",
                   re.IGNORECASE)
DAY_OFFSET = re.compile(r"\b(aj|aaj|ajke|aajke|today|kal|kaal|kalke|tomorrow|porshu|porsu|"
                        r"day\s+after\s+tomorrow)\b|আজ|আগামীকাল|কাল|পরশু", re.IGNORECASE)
_OFFSETS = {"aj": 0, "aaj": 0, "ajke": 0, "aajke": 0, "today": 0, "আজ": 0, "kal": 1,
            "kaal": 1, "kalke": 1, "tomorrow": 1, "আগামীকাল": 1, "কাল": 1, "porshu": 2,
            "porsu": 2, "পরশু": 2}
TASK_DONE = re.compile(r"(?:task|kaj|কাজ)\s*#?\s*(\d+)\s*(?:ta\s*)?(?:shesh|sesh|done|finish\w*|"
                       r"complete\w*|শেষ)\s*(?:hole|holei|hoile|হলে|হলেই)?", re.IGNORECASE)
FILE_COND = re.compile(r"([A-Za-z]:\\[^\s\"']+)\s*(?:file\s*)?(?:ashle|ele|asle|aile|thakle|"
                       r"আসলে|এলে|থাকলে|exists?|appears?)", re.IGNORECASE)
REMIND = re.compile(r"\bremind\w*(?:\s+me)?\b|mone\s*kori(?:ye|e|a)?\s*(?:dio|diyo|diben|dao)?|"
                    r"মনে\s*করি(?:য়|য)ে\s*\S*|\b(?:amake|amay|আমাকে)\s*(?:bolo|bolben|janao|"
                    r"janaben|jaaniyo|janiyo|জানাও|বলো|জানাবে)\b|\balarm\b", re.IGNORECASE)
FILLER = re.compile(r"\b(please|plz|pls|ekta|schedule\s*koro|set\s*koro|amake|amay|"
                    r"amar\s*jonno|ke|at|on|by|then|tokhon|তখন|আমাকে)\b", re.IGNORECASE)


_ACTION_VERB = re.compile(r"\b(koro|korbe|koren|chalao|chalu\s*koro|run|bolo|janao|dao|"
                          r"pathao|dekhao|kholo|nao|start|send|check|do)\b|করো|করবে|চালাও|"
                          r"বলো|জানাও|দাও|পাঠাও|দেখাও|খোলো", re.IGNORECASE)
_QUESTION = re.compile(r"\?|\b(ki|kí|kokhon|koyta|koto|keno|what|when|which|is\s+there)\b|"
                       r"কী|কি|কখন|কয়টা|কত", re.IGNORECASE)


def looks_scheduled(text: str) -> bool:
    """A request to do something LATER — not a question that merely mentions a time
    ("ajke 5 tay ki match ache?" is a question)."""
    t = text.translate(_DIGITS)
    if REMIND.search(t) or TASK_DONE.search(t) or FILE_COND.search(t):
        return True
    if DELAY.search(t) or EVERY_N.search(t) or DAILY.search(t) or WEEKLY.search(t):
        return not (_QUESTION.search(t) and not _ACTION_VERB.search(t))
    if CLOCK.search(t):
        return bool(_ACTION_VERB.search(t)) and not _QUESTION.search(t)
    return False


@dataclass
class ScheduleSpec:
    kind: str                         # once | delayed | recurring | condition
    action: str                       # task | remind
    what: str
    first_run: datetime | None        # local time (aware); None for condition jobs
    rule: dict[str, Any] | None = None
    condition: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)


def _clock(text: str, now: datetime) -> tuple[int, int] | None:
    m = CLOCK.search(text)
    if m is None:
        return None
    if m.group(4) is not None:
        hour, minute, suffix = int(m.group(4)), int(m.group(5)), ""
    elif m.group(6) is not None:
        hour, minute, suffix = int(m.group(6)), int(m.group(7) or 0), (m.group(8) or "").lower()
    else:
        hour, minute, suffix = int(m.group(1)), int(m.group(2) or 0), m.group(3).lower()
    if hour > 23 or minute > 59:
        return None
    period = PERIOD.search(text)
    p = PERIODS[period.group(1).lower()] if period else ""
    if suffix.startswith("p") and hour < 12:
        hour += 12
    elif suffix.startswith("a") and hour == 12:
        hour = 0
    elif not suffix.startswith(("a", "p")) and hour <= 12:
        if p == "night":
            # raat 9-11 → 21-23; raat 12 → 00; raat 1-4 → 01-04 (after midnight)
            hour = 0 if hour == 12 else hour + 12 if hour >= 5 else hour
        elif p in ("pm",) and hour < 12:
            hour += 12
        elif p == "noon" and hour < 6:
            hour += 12                         # dupur 1-3 tay → 13-15
        elif p == "" and hour <= 12 and m.group(4) is None:
            # no am/pm, no period: the next time this clock reading comes round
            candidates = [hour % 12, hour % 12 + 12]
            soonest = min(candidates, key=lambda h: (h * 60 + minute - now.hour * 60
                                                     - now.minute) % 1440)
            hour = soonest
    return hour, minute


def _strip(text: str, spans: list[tuple[int, int]]) -> str:
    keep, last = [], 0
    for a, b in sorted(spans):
        if a >= last:
            keep.append(text[last:a])
            last = b
    keep.append(text[last:])
    out = " ".join("".join(keep).split())
    out = FILLER.sub(" ", out)
    out = re.sub(r"\s+", " ", out)
    return re.sub(r"^[\s,.:;\-–—।]+|[\s,.:;\-–—।]+$", "", out).strip()


def parse_schedule(text: str, now: datetime) -> ScheduleSpec | None:
    """`now` is local, aware. None = no time / condition found (ask the owner)."""
    t = text.translate(_DIGITS)
    spans: list[tuple[int, int]] = []

    def take(m: re.Match[str] | None) -> re.Match[str] | None:
        if m is not None:
            spans.append(m.span())
        return m

    remind = take(REMIND.search(t)) is not None
    kind = ""
    first: datetime | None = None
    rule: dict[str, Any] | None = None
    cond: dict[str, Any] | None = None
    if (m := take(TASK_DONE.search(t))) is not None:
        kind, cond = "condition", {"type": "task_finished", "task_id": int(m.group(1))}
    elif (m := take(FILE_COND.search(t))) is not None:
        kind, cond = "condition", {"type": "file_exists", "path": m.group(1)}
    elif (m := take(EVERY_N.search(t))) is not None:
        minutes = max(1, round(float(m.group(1)) * UNIT_MINUTES[m.group(2).lower()]))
        kind, rule = "recurring", {"every_minutes": minutes}
        first = now + timedelta(minutes=minutes)
    elif (m := take(DELAY.search(t))) is not None:
        n, unit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        kind = "delayed"
        first = now + timedelta(minutes=float(n) * UNIT_MINUTES[unit.lower()])
    else:
        weekly = take(WEEKLY.search(t))
        daily = take(DAILY.search(t))
        clock = _clock(t, now)
        if clock is None:
            return None
        cm = CLOCK.search(t)
        take(cm)
        for pm in PERIOD.finditer(t):
            spans.append(pm.span())
        hh, mm = clock
        if weekly is not None:
            day = _DAY_WORDS[weekly.group(1).lower()]
            kind, rule = "recurring", {"every": "week", "days": [day], "at": f"{hh:02d}:{mm:02d}"}
        elif daily is not None:
            kind, rule = "recurring", {"every": "day", "at": f"{hh:02d}:{mm:02d}"}
        else:
            kind = "once"
        off = take(DAY_OFFSET.search(t))
        base = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if off is not None and kind == "once":
            base += timedelta(days=_OFFSETS.get(off.group(0).lower().replace(" ", ""), 0))
            first = base if base > now else None
            if first is None:
                return ScheduleSpec("once", "remind" if remind else "task",
                                    _strip(t, spans), None, notes=["time is in the past"])
        else:
            first = next_occurrence(rule, now) if rule else (
                base if base > now else base + timedelta(days=1))
    what = _strip(t, spans)
    return ScheduleSpec(kind, "remind" if remind else "task", what, first, rule, cond)


def next_occurrence(rule: dict[str, Any] | None, after: datetime) -> datetime | None:
    """The next run strictly after `after` (local, aware)."""
    if not rule:
        return None
    if "every_minutes" in rule:
        return after + timedelta(minutes=int(rule["every_minutes"]))
    hh, mm = (int(x) for x in str(rule.get("at", "09:00")).split(":"))
    candidate = after.replace(hour=hh, minute=mm, second=0, microsecond=0)
    days = set(rule.get("days") or range(7)) if rule.get("every") == "week" else set(range(7))
    for i in range(0, 8):
        c = candidate + timedelta(days=i)
        if c > after and c.weekday() in days:
            return c
    return None
