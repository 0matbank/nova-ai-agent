"""Spoken replies (plan §57): voice in → voice out, text always too."""

from __future__ import annotations

import asyncio
import datetime as dt
from pathlib import Path

import pytest

from channels.base import IncomingMessage, OutgoingMessage, VoiceRef
from core.config.schema import EXEMPT_MESSAGE_TYPES, NotificationThrottle, TTSSection
from core.notify.notifier import MessageType, Notifier
from core.voice.intake import VoiceIntake
from core.voice.tts import Speaker, TTSBudget, language_of, speakable
from tests.integration.test_voice import SETTINGS, Echo, FakeModel, fake_download, make


def test_speakable_strips_noise() -> None:
    text = ("✅ TASK #3 COMPLETE\nবাংলাদেশের রাজধানী **ঢাকা** 🇧🇩\n"
            "দেখুন https://example.com\nVerifier: answer from ollama_local\n/task 3")
    out = speakable(text)
    assert "Verifier" not in out and "http" not in out and "**" not in out and "✅" not in out
    assert "ঢাকা" in out and "/task" not in out
    assert len(speakable("শব্দ। " * 500, max_chars=100)) <= 101


def test_symbols_read_as_words() -> None:
    en = speakable("CPU: 4%\nRAM: 79% (12.0/15.1 GB)\nGPU: 38°C")
    assert "4 percent" in en and "12.0 of 15.1 gigabytes" in en and "38 degrees" in en
    assert "%" not in en and "(" not in en
    bn = speakable("আপনার র‍্যাম ৭৯% ব্যবহার হচ্ছে, 12/16 GB")
    assert "শতাংশ" in bn and "16-এর মধ্যে 12 গিগাবাইট" in bn


def test_voice_file_type() -> None:
    from channels.telegram.api import _voice_file
    assert _voice_file(b"OggS....")[2] == "audio/ogg"
    assert _voice_file(b"ID3....")[2] == "audio/mpeg"


@pytest.mark.parametrize("text,lang", [("ঢাকা বাংলাদেশের রাজধানী", "bn"),
                                       ("CPU is at 4 percent", "en"),
                                       ("আপনার PC ভালো আছে", "bn")])
def test_language_of(text: str, lang: str) -> None:
    assert language_of(text) == lang


class FakeSpeaker(Speaker):
    def __init__(self) -> None:
        super().__init__(TTSSection(enabled=True, voices={"bn": "x"}))
        self.said: list[str] = []

    async def synthesize(self, text: str) -> bytes | None:
        self.said.append(text)
        return b"OggS-voice"


def voice_msg() -> IncomingMessage:
    return IncomingMessage("telegram", "555", "555", "9", "", dt.datetime.now(dt.UTC),
                           VoiceRef("f", 3, "audio/ogg", 10))


class TaskEcho(Echo):
    async def __call__(self, msg: IncomingMessage) -> OutgoingMessage:
        self.got.append(msg.text)
        return OutgoingMessage("📥 Task #7 তৈরি হয়েছে", task_id=7)


def test_task_answer_is_spoken_later_not_the_ack(tmp_path: Path) -> None:
    t, _ = make(tmp_path, FakeModel(("বাংলাদেশের রাজধানী কী?", "bn", -0.05)))
    sp = FakeSpeaker()
    vi = VoiceIntake(t, SETTINGS, tmp_path / "a", TaskEcho(), speaker=sp)
    out = asyncio.run(vi.handle(voice_msg(), fake_download))
    assert out.voice is None and out.task_id == 7 and 7 in vi.voice_tasks
    assert sp.said == []                       # "task created" is not read aloud


def test_confirmation_question_is_spoken(tmp_path: Path) -> None:
    t, _ = make(tmp_path, FakeModel(("ফাইলটা ডিলিট কর", "bn", -1.5)))
    sp = FakeSpeaker()
    vi = VoiceIntake(t, SETTINGS, tmp_path / "a", Echo(), speaker=sp)
    out = asyncio.run(vi.handle(voice_msg(), fake_download))
    assert out.voice == b"OggS-voice" and "ঠিক শুনেছি তো" in sp.said[0]


def test_notifier_final_hook_only_for_final_messages() -> None:
    calls: list[tuple[str, MessageType, int]] = []

    async def send(chat_id: str, msg: OutgoingMessage) -> None:
        pass

    async def hook(chat_id: str, kind: MessageType, task_id: int) -> None:
        calls.append((chat_id, kind, task_id))
    n = Notifier(NotificationThrottle(
        min_progress_interval_seconds=30, soft_max_progress_updates_per_task=5,
        repeated_error_cooldown_seconds=300, batch_small_results=True,
        exempt_message_types=list(EXEMPT_MESSAGE_TYPES)), send)
    n.on_task_final.append(hook)
    asyncio.run(n.notify("c", MessageType.INFO, "x", task_id=1))
    asyncio.run(n.notify("c", MessageType.TASK_COMPLETED, "done", task_id=1))
    asyncio.run(n.notify("c", MessageType.TASK_FAILED_PERMANENTLY, "no", task_id=2))
    assert calls == [("c", MessageType.TASK_COMPLETED, 1),
                     ("c", MessageType.TASK_FAILED_PERMANENTLY, 2)]


def test_channel_sends_voice_then_text(tmp_path: Path) -> None:
    from channels.telegram.auth import ChatWhitelist
    from channels.telegram.channel import TelegramChannel
    from core.log import setup_logging, shutdown_logging
    from tests.mocks.telegram import OWNER, FakeTelegram

    setup_logging(tmp_path / "logs", ["core"], worker="t", console=False)
    try:
        fake = FakeTelegram()

        async def h(m):  # type: ignore[no-untyped-def]
            return None
        ch = TelegramChannel(fake.api(), ChatWhitelist([str(OWNER)]), h, poll_timeout=1)
        asyncio.run(ch.send(str(OWNER), OutgoingMessage("উত্তর", voice=b"OggS" + b"x" * 200)))
        asyncio.run(ch.send(str(OWNER), OutgoingMessage("", voice=b"OggS" + b"y" * 50)))
        assert [v["size"] for v in fake.voices] == [204, 54]
        assert fake.texts_to(OWNER) == ["উত্তর"]          # voice-only message sends no text
    finally:
        shutdown_logging()


def test_disabled_tts_returns_none() -> None:
    assert asyncio.run(Speaker(TTSSection(enabled=False)).synthesize("hi")) is None


@pytest.mark.e2e
def test_real_bangla_tts() -> None:
    """Online: Microsoft neural voice → OGG/Opus."""
    sp = Speaker(TTSSection(enabled=True, voices={"bn": "bn-BD-NabanitaNeural"}))
    audio = asyncio.run(sp.synthesize("বাংলাদেশের রাজধানী ঢাকা।"))
    if audio is None:
        pytest.skip("TTS service not reachable")
    assert len(audio) > 2000 and (audio[:3] == b"ID3" or audio[0] == 0xFF)   # MP3


# ------------------------------------------------------------- Gemini voice

def _wav() -> bytes:
    import io
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x10" * 24000)
    return buf.getvalue()


def _gemini(status: int = 200, calls: list | None = None):  # type: ignore[no-untyped-def]
    import base64

    import httpx
    from pydantic import SecretStr

    from core.voice.tts import GeminiTTS

    def handler(req: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(req)
        if status != 200:
            return httpx.Response(status, json={"error": "x"})
        return httpx.Response(200, json={"steps": [{"type": "model_output", "content": [
            {"type": "audio", "data": base64.b64encode(_wav()).decode()}]}]})
    return GeminiTTS(SecretStr("test-gemini-key-123"), "gemini-3.8-flash-tts", "Kore",
                     "warm", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_gemini_voice_preferred_and_ogg(tmp_path: Path) -> None:
    import json
    calls: list = []
    sp = Speaker(TTSSection(enabled=True, engine="gemini", voices={"bn": "x"}),
                 _gemini(calls=calls), TTSBudget(tmp_path / "b.json", 1000))
    audio = asyncio.run(sp.synthesize("বাংলাদেশের রাজধানী ঢাকা।"))
    assert audio is not None and audio[:4] == b"OggS" and sp.last_engine == "gemini"
    body = json.loads(calls[0].content)
    assert calls[0].headers["x-goog-api-key"] == "test-gemini-key-123"
    assert body["model"] == "gemini-3.8-flash-tts"
    assert body["generation_config"]["speech_config"] == [{"voice": "Kore"}]
    assert body["input"][0]["content"][0]["annotations"][0]["style"] == "warm"


@pytest.mark.parametrize("status", [429, 403, 500])
def test_gemini_problem_falls_back_to_edge(tmp_path: Path, status: int) -> None:
    class EdgeStub(Speaker):
        async def _edge(self, clean: str) -> bytes | None:
            self.last_engine = "edge"
            return b"ID3edge"
    sp = EdgeStub(TTSSection(enabled=True, engine="gemini", voices={"bn": "x"}),
                  _gemini(status), TTSBudget(tmp_path / "b.json", 1000))
    assert asyncio.run(sp.synthesize("হ্যালো")) == b"ID3edge" and sp.last_engine == "edge"


def test_budget_guard_persists_and_blocks(tmp_path: Path) -> None:
    b = TTSBudget(tmp_path / "b.json", daily_chars=10)
    assert b.allows(10)
    b.spend(8)
    assert not TTSBudget(tmp_path / "b.json", 10).allows(5)   # survives restart
    assert TTSBudget(tmp_path / "b.json", 10).allows(2)
