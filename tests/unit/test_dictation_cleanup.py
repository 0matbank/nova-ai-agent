"""V1.2 dictation cleanup (owner 2026-10-26): misheard words, fillers, stutters and
self-corrections fixed by this PC's AI — but the meaning, names, numbers and every «না»
stay, or the heard words are typed. Measured on the owner's 20 recordings: character
errors 13.2% → ~12%, none worse; the safety set 8/8; ~0.8 s."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from core.voice.cleanup import clean, guard

HEARD = "আপনার কোমান্ড এখন আর প্রসেসের তালিতাই দেখা জাইনা"


@pytest.mark.parametrize(("heard", "cleaned", "ok"), [
    # the owner's real dictation: misheard words fixed, a glued «জাইনা» split back
    (HEARD, "আপনার কমান্ড এখন আর প্রসেসের তালিকায় দেখা যায় না।", True),
    (HEARD, "আপনার কমান্ড এখন আর প্রসেসের তালিকায় যায় না।", False),       # «দেখা» dropped
    ("পিসির বড্ধমন স্ট্যাটাস পলো", "পিসির বর্তমান স্ট্যাটাস বলো।", True),
    ("রিপোর্ট পাঠাতে পারব না", "রিপোর্ট পাঠাতে পারব।", False),           # a «না» lost
    ("রিপোর্ট পাঠাতে পারবোনা", "রিপোর্ট পাঠাতে পারবো না।", True),        # glued না kept
    ("এটা ঠিক", "এটা ঠিক নয়।", False),                                    # a «নয়» added
    ("আমি ৩০০ টাকা পাঠিয়েছি", "আমি ৫০০ টাকা পাঠিয়েছি।", False),        # a number changed
    ("আমি আমি কাল সকাল ১০টায় আসব", "আমি কাল সকাল ১০টায় আসব।", True),   # a stutter
    ("ভাইয়া উম রিপোর্টটা পাঠাব", "ভাইয়া, রিপোর্টটা পাঠাব।", True),     # a filler
    ("মিটিং পাঁচটায় না না ছয়টায় হবে", "মিটিং ছয়টায় হবে।", True),       # a correction
    ("আমাকে একটা কবিতা লিখে দাও",
     "বৃষ্টি পড়ে টাপুর টুপুর মেঘের দেশে নাচে নূপুর আকাশ জুড়ে", False),  # obeyed
    ("এই লেখাটা ঠিক করে দাও", "", False),
])
def test_guard(heard: str, cleaned: str, ok: bool) -> None:
    assert (guard(heard, cleaned) == "") is ok


def test_the_owners_own_words_never_change() -> None:
    assert guard("রুমানকে বলো আমি আসছি", "রমজানকে বলো আমি আসছি।", {"রুমানকে"}) != ""
    assert guard("রুমানকে বলো আমি আসছি", "রুমানকে বলো, আমি আসছি।", {"রুমানকে"}) == ""


@dataclass
class R:
    ok: bool
    answer: str
    error: str = ""


class Router:
    def __init__(self, answer: str, ok: bool = True) -> None:
        self.r = R(ok, answer)
        self.requests: list = []

    async def complete(self, req):  # type: ignore[no-untyped-def]
        self.requests.append(req)
        return self.r


def test_clean_uses_the_local_ai_and_its_answer() -> None:
    router = Router("<cleaned>পিসির বর্তমান স্ট্যাটাস বলো।</cleaned>")
    c = asyncio.run(clean(router, "পিসির বড্ধমন স্ট্যাটাস পলো", hints=["স্ট্যাটাস"]))
    assert c.used_ai and c.text == "পিসির বর্তমান স্ট্যাটাস বলো।"
    req = router.requests[0]
    assert req.privacy == "local_only" and req.think is False
    assert "স্ট্যাটাস" in req.user_request and "<dictation>" in req.user_request


@pytest.mark.parametrize(("answer", "ok"), [
    ("Here is the cleaned text: পিসির অবস্থা বলো", True),       # no tags
    ("<cleaned>পিসির অবস্থা বলো না</cleaned>", True),           # a «না» added
    ("", False),                                                # the AI is unavailable
])
def test_anything_off_types_the_heard_words(answer: str, ok: bool) -> None:
    c = asyncio.run(clean(Router(answer, ok), "পিসির অবস্থা বলো"))
    assert not c.used_ai and c.text == "পিসির অবস্থা বলো" and c.reason


def test_pc_dictation_types_the_cleaned_words(tmp_path: Path) -> None:
    from channels.base import IncomingMessage, OutgoingMessage
    from core.voice import pc_audio
    from core.voice.intake import VoiceIntake
    from core.voice.ptt import pc_turn
    from tests.integration.test_push_to_talk import tone
    from tests.integration.test_voice import SETTINGS, FakeModel, make
    t, _ = make(tmp_path, FakeModel((HEARD, "bn", -0.05)))
    routed: list[str] = []
    sent: list[OutgoingMessage] = []

    async def make_task(msg: IncomingMessage) -> OutgoingMessage:
        routed.append(msg.text)
        return OutgoingMessage("📥 Task #3", task_id=3)

    async def send(chat: str, out: OutgoingMessage) -> None:
        sent.append(out)
    fixed = "আপনার কমান্ড এখন আর প্রসেসের তালিকায় দেখা যায় না।"
    vi = VoiceIntake(t, SETTINGS, tmp_path / "a", make_task,
                     cleaner=lambda heard: clean(Router(f"<cleaned>{fixed}</cleaned>"), heard))
    asyncio.run(pc_turn(vi, send, "555", pc_audio.to_wav(tone(1.0)), 1.0, None,
                        dictation=True))
    assert routed == [f"লিখে দাও: {fixed}"]
    assert f"«{HEARD}»" in sent[0].text and f"✍️ পরিষ্কার করে লিখছি: «{fixed}»" in sent[0].text
