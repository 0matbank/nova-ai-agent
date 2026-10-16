"""Phase 19: scheduling requests in Bangla / Banglish / English → exact times (plan §39)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from core.scheduler import next_occurrence, parse_schedule

TZ = ZoneInfo("Asia/Dhaka")
NOW = datetime(2026, 10, 16, 15, 30, tzinfo=TZ)          # Friday 3:30 pm


@pytest.mark.parametrize(("text", "kind", "action", "first", "what"), [
    ("30 minute por download status bolo", "delayed", "task", "2026-10-16 16:00",
     "download status bolo"),
    ("২ ঘণ্টা পর pc status bolo", "delayed", "task", "2026-10-16 17:30", "pc status bolo"),
    ("in 10 minutes remind me to stretch", "delayed", "remind", "2026-10-16 15:40",
     "to stretch"),
    ("raat 2 tay scanner run koro", "once", "task", "2026-10-17 02:00", "scanner run koro"),
    ("raat 10 tay backup koro", "once", "task", "2026-10-16 22:00", "backup koro"),
    ("kal shokal 9 tay amake mone koriye dio meeting ache", "once", "remind",
     "2026-10-17 09:00", "meeting ache"),
    ("bikel 5 tay amake bolo pani khete", "once", "remind", "2026-10-16 17:00", "pani khete"),
    ("আজ রাত ১১টায় মনে করিয়ে দিও ওষুধ খেতে", "once", "remind", "2026-10-16 23:00",
     "ওষুধ খেতে"),
    ("at 9:15 pm send me the pc status", "once", "task", "2026-10-16 21:15",
     "send me the pc status"),
    ("4 tay pc status bolo", "once", "task", "2026-10-16 16:00", "pc status bolo"),
    ("protidin shokal 9 tay website health check koro", "recurring", "task",
     "2026-10-17 09:00", "website health check koro"),
    ("every day at 18:00 pc status bolo", "recurring", "task", "2026-10-16 18:00",
     "pc status bolo"),
    ("proti shukrobar raat 10 tay backup koro", "recurring", "task", "2026-10-16 22:00",
     "backup koro"),
    ("every monday at 8am weekly report banao", "recurring", "task", "2026-10-19 08:00",
     "weekly report banao"),
    ("proti 2 ghonta por por pc status bolo", "recurring", "task", "2026-10-16 17:30",
     "pc status bolo"),
])
def test_times_are_understood(text: str, kind: str, action: str, first: str,
                              what: str) -> None:
    spec = parse_schedule(text, NOW)
    assert spec is not None, text
    assert (spec.kind, spec.action, spec.what) == (kind, action, what), spec
    assert spec.first_run is not None and spec.first_run.strftime("%Y-%m-%d %H:%M") == first


def test_conditions() -> None:
    s = parse_schedule("task #12 shesh hole amake janao", NOW)
    assert s is not None and s.kind == "condition" and s.action == "remind"
    assert s.condition == {"type": "task_finished", "task_id": 12} and s.first_run is None
    f = parse_schedule(r"D:\inbox\report.pdf file ashle amake bolo", NOW)
    assert f is not None and f.condition == {"type": "file_exists",
                                             "path": r"D:\inbox\report.pdf"}


def test_a_position_in_a_video_is_not_a_schedule() -> None:
    from core.scheduler.parse import looks_scheduled
    assert not looks_scheduled("clip.mp4 video ta theke 2 second e ekta thumbnail banao")
    assert not looks_scheduled("video ta 10 second theke 20 second porjonto trim koro")
    assert looks_scheduled("2 second por amake bolo")


def test_no_time_means_ask_again_and_past_is_noticed() -> None:
    assert parse_schedule("scanner run koro", NOW) is None
    past = parse_schedule("aj shokal 9 tay scanner run koro", NOW)
    assert past is not None and past.first_run is None and "past" in past.notes[0]


def test_next_occurrence_rolls_forward() -> None:
    assert next_occurrence({"every": "day", "at": "09:00"}, NOW).day == 17
    fri = next_occurrence({"every": "week", "days": [4], "at": "22:00"}, NOW)
    assert fri is not None and fri.strftime("%a %H:%M") == "Fri 22:00"
    after = next_occurrence({"every": "week", "days": [4], "at": "22:00"},
                            NOW.replace(hour=23))
    assert after is not None and after.day == 23
    assert next_occurrence({"every_minutes": 90}, NOW).strftime("%H:%M") == "17:00"
