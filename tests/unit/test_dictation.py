"""V1.2 dictation: «লিখে দাও: …» types the words at the cursor; spoken punctuation
becomes marks; the push-to-talk dictation key sends exactly that, silently."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from channels.base import IncomingMessage, OutgoingMessage
from core.router.ack import ack_for
from core.router.dictation import dictation_command, with_marks
from core.router.intent import classify_by_rules, is_quick_request


@pytest.mark.parametrize(("text", "words"), [
    ("লিখে দাও: আমি আসছি দাঁড়ি নতুন লাইন ধন্যবাদ", "আমি আসছি।\nধন্যবাদ"),
    ("type: hello comma world full stop", "hello, world."),
    ("dictate: what is this question mark", "what is this?"),
    ("টাইপ করো: কাল সকাল ১০টায় মিটিং আছে", "কাল সকাল ১০টায় মিটিং আছে"),
    ("likhe dao: Rumanbank er report", "Rumanbank er report"),
])
def test_the_words_after_the_colon_are_typed(text: str, words: str) -> None:
    assert dictation_command(text) == ("desktop-ui", "dictate", {"text": words})


@pytest.mark.parametrize("text", [
    "একটা কবিতা লিখে দাও",              # a request to write, not words to type
    "notepad e likhe dao hello",         # work inside an app (the Windows Operator)
    "টাইপ করো:   ",
    "type the report",
])
def test_not_dictation(text: str) -> None:
    assert dictation_command(text) is None


def test_marks_only_as_whole_words() -> None:
    assert with_marks("কমান্ড চালাও কমা তারপর") == "কমান্ড চালাও, তারপর"


def test_dictation_wins_over_every_other_rule() -> None:
    hit = classify_by_rules("লিখে দাও: কাল সকাল ১০টায় notepad খুলে মিটিং")
    assert hit is not None and hit.skill == (
        "desktop-ui", "dictate", {"text": "কাল সকাল ১০টায় notepad খুলে মিটিং"})
    assert is_quick_request("লিখে দাও: হ্যালো")
    assert ack_for("লিখে দাও: হ্যালো") == "⌨️ cursor-এর জায়গায় লিখছি"


def test_pc_dictation_key_sends_the_words_and_stays_quiet(tmp_path: Path) -> None:
    from core.voice import pc_audio
    from core.voice.intake import VoiceIntake
    from core.voice.ptt import pc_turn
    from tests.integration.test_push_to_talk import tone
    from tests.integration.test_voice import SETTINGS, FakeModel, make
    t, _ = make(tmp_path, FakeModel(("আমি আসছি দাঁড়ি", "bn", -0.05)))
    routed: list[str] = []
    said: list[str] = []
    sent: list[OutgoingMessage] = []

    async def make_task(msg: IncomingMessage) -> OutgoingMessage:
        routed.append(msg.text)
        return OutgoingMessage("📥 Task #3", task_id=3)

    async def send(chat: str, out: OutgoingMessage) -> None:
        sent.append(out)

    async def here(text: str) -> None:
        said.append(text)
    vi = VoiceIntake(t, SETTINGS, tmp_path / "audio", make_task)

    async def go() -> None:
        await pc_turn(vi, send, "555", pc_audio.to_wav(tone(1.0)), 1.0, here, dictation=True)
        for _ in range(10):
            await asyncio.sleep(0)
    asyncio.run(go())
    assert routed == ["লিখে দাও: আমি আসছি দাঁড়ি"]
    assert dictation_command(routed[0]) == ("desktop-ui", "dictate", {"text": "আমি আসছি।"})
    assert sent[0].text.startswith("⌨️ PC-তে dictation")
    assert said == [] and vi.here(3) is None and 3 not in vi.voice_tasks   # no voice
