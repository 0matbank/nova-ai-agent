"""V1.1 voice speed + accuracy: Bangla/English only (no Devanagari), one decoding pass,
warm-idle with the Resource Manager, cold load overlapping the download, harmless app
opens without an extra ✅, spoken replies that never hold the text back."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from channels.base import IncomingMessage, OutgoingMessage
from core.scheduler.parse import looks_scheduled
from core.voice.intake import VoiceIntake
from core.voice.transcriber import Transcriber
from tests.integration.test_voice import SETTINGS, Echo, Info, Seg, fake_download, voice_msg


class DetectingModel:
    """faster-whisper with language detection: Banglish heard as Hindi."""

    def __init__(self, detected: str = "hi", ranked: list[tuple[str, float]] | None = None,
                 text: str = "পিসি স্ট্যাটাস বলো", logprob: float = -0.05) -> None:
        self.detected = detected
        self.ranked = ranked or [("hi", 0.6), ("bn", 0.3), ("en", 0.1)]
        self.text, self.logprob = text, logprob
        self.calls: list[dict[str, Any]] = []
        self.detect_calls = 0

    def detect_language(self, audio: Any, **kw: Any) -> tuple[str, float, list[Any]]:
        self.detect_calls += 1
        return self.detected, self.ranked[0][1], self.ranked

    def transcribe(self, audio: Any, **kw: Any) -> tuple[list[Seg], Info]:
        self.calls.append(kw)
        return [Seg(self.text, self.logprob)], Info(kw.get("language") or "xx", 0.9)


def transcriber(tmp_path: Path, model: Any, **kw: Any) -> Transcriber:
    (tmp_path / "model").mkdir(exist_ok=True)
    t = Transcriber(tmp_path / "model", factory=lambda *a: model, **kw)
    t.decoder = lambda path, sampling_rate: [0.0] * 16000     # decoded samples
    return t


def test_hindi_guess_becomes_bangla_in_one_pass(tmp_path: Path) -> None:
    m = DetectingModel()
    r = transcriber(tmp_path, m).transcribe_sync(tmp_path / "model")
    assert r.language == "bn" and r.text == "পিসি স্ট্যাটাস বলো"
    assert m.detect_calls == 1 and [c["language"] for c in m.calls] == ["bn"]   # no 2nd pass


def test_english_is_kept_when_it_is_more_likely(tmp_path: Path) -> None:
    m = DetectingModel("fr", [("fr", 0.5), ("en", 0.4), ("bn", 0.1)], "open capcut")
    r = transcriber(tmp_path, m).transcribe_sync(tmp_path / "model")
    assert r.language == "en" and [c["language"] for c in m.calls] == ["en"]


def test_hotwords_reach_whisper(tmp_path: Path) -> None:
    m = DetectingModel("bn", [("bn", 0.9)])
    t = transcriber(tmp_path, m)
    t.hotwords = "Premiere, CapCut, OBS"
    t.transcribe_sync(tmp_path / "model")
    assert m.calls[0]["hotwords"] == "Premiere, CapCut, OBS"


def test_warm_idle_follows_memory_pressure(tmp_path: Path) -> None:
    level = ["normal"]
    t = transcriber(tmp_path, DetectingModel("bn", [("bn", 0.9)]),
                    idle_unload_seconds=1200, pressure=lambda: level[0])
    t.transcribe_sync(tmp_path / "model")
    now = t._last_used + 120
    assert not t.unload_if_idle(now)                 # 2 min later: still warm
    level[0] = "high"
    assert t.unload_if_idle(now) and not t.loaded    # memory short → given back sooner
    t.transcribe_sync(tmp_path / "model")
    level[0] = "critical"
    asyncio.run(t.relieve())                         # Resource Manager critical action
    assert not t.loaded


def test_cold_load_overlaps_the_download(tmp_path: Path) -> None:
    order: list[str] = []

    def slow_factory(*a: Any) -> DetectingModel:
        order.append("load-start")
        time.sleep(0.3)
        order.append("load-end")
        return DetectingModel("bn", [("bn", 0.9)])
    (tmp_path / "model").mkdir()
    t = Transcriber(tmp_path / "model", factory=slow_factory)
    t.decoder = lambda path, sampling_rate: [0.0]

    async def slow_download(file_id: str) -> bytes:
        order.append("download-start")
        await asyncio.sleep(0.3)
        order.append("download-end")
        return b"OggS"
    vi = VoiceIntake(t, SETTINGS, tmp_path / "a", Echo())
    t0 = time.perf_counter()
    asyncio.run(vi.handle(voice_msg(), slow_download))
    assert time.perf_counter() - t0 < 0.55            # 0.3 + 0.3 would be serial
    assert order.index("download-start") < order.index("load-end")
    assert t.last_load_seconds >= 0.25


def test_clear_app_open_needs_no_extra_confirm(tmp_path: Path) -> None:
    # confidence 0.74: below the 0.85 action threshold, above the 0.5 harmless one
    t = transcriber(tmp_path, DetectingModel("bn", [("bn", 0.9)], "প্রিমিয়ার প্রো খোলো",
                                             logprob=-0.29))
    echo = Echo()
    vi = VoiceIntake(t, SETTINGS, tmp_path / "a", echo, low_risk=lambda s: "খোলো" in s)
    out = asyncio.run(vi.handle(voice_msg(), fake_download))
    assert echo.got == ["প্রিমিয়ার প্রো খোলো"] and "ঠিক শুনেছি" not in out.text
    # closing is not "low risk": the same confidence still asks first
    t2 = transcriber(tmp_path, DetectingModel("bn", [("bn", 0.9)], "প্রিমিয়ার প্রো বন্ধ করো",
                                              logprob=-0.29))
    echo2 = Echo()
    vi2 = VoiceIntake(t2, SETTINGS, tmp_path / "b", echo2, low_risk=lambda s: "খোলো" in s)
    out2 = asyncio.run(vi2.handle(voice_msg(), fake_download))
    assert echo2.got == [] and "ঠিক শুনেছি" in out2.text


class SlowSpeaker:
    enabled = True

    def __init__(self) -> None:
        self.gate = asyncio.Event()

    async def synthesize(self, text: str) -> bytes | None:
        await self.gate.wait()
        return b"OggS-voice"


def test_spoken_reply_follows_the_text(tmp_path: Path) -> None:
    """The text answer goes out at once; the voice follows when TTS is done."""
    sent: list[tuple[str, OutgoingMessage]] = []

    async def send(chat_id: str, msg: OutgoingMessage) -> None:
        sent.append((chat_id, msg))

    class Direct(Echo):
        async def __call__(self, msg: IncomingMessage) -> OutgoingMessage:
            return OutgoingMessage("CPU: 5%")                  # a direct reply, no task
    sp = SlowSpeaker()
    t = transcriber(tmp_path, DetectingModel("bn", [("bn", 0.9)]))
    vi = VoiceIntake(t, SETTINGS, tmp_path / "a", Direct(), speaker=sp, send=send)

    async def go() -> OutgoingMessage:
        out = await asyncio.wait_for(vi.handle(voice_msg(), fake_download), timeout=2)
        assert out.voice is None and sent == []               # text first, not held back
        sp.gate.set()
        while vi._speaking:
            await asyncio.sleep(0.01)
        return out
    out = asyncio.run(go())
    assert "CPU: 5%" in out.text and sent and sent[0][1].voice == b"OggS-voice"


def test_voice_task_end_to_end_time_is_logged(tmp_path: Path) -> None:
    class Queued(Echo):
        async def __call__(self, msg: IncomingMessage) -> OutgoingMessage:
            return OutgoingMessage("📥 Task #7", task_id=7)
    t = transcriber(tmp_path, DetectingModel("bn", [("bn", 0.9)]))
    vi = VoiceIntake(t, SETTINGS, tmp_path / "a", Queued())
    asyncio.run(vi.handle(voice_msg(), fake_download))
    assert 7 in vi.voice_tasks
    started = vi.finish(7)
    assert started is not None and time.time() - started < 5 and vi.finish(7) is None


def test_tell_me_is_not_a_schedule() -> None:
    # 2026-10-16 bench: "নোভা পিসির স্টেটাস আমাকে জানাও" became a reminder
    assert not looks_scheduled("নোভা পিসির স্টেটাস আমাকে জানাও")
    assert not looks_scheduled("pc status amake bolo")
    assert looks_scheduled("30 minute por pc status amake janao")
    assert looks_scheduled("task #12 shesh hole amake janao")
    assert looks_scheduled("amake mone koriye dio pani khete")

