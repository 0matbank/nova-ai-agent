"""V1.2 push-to-talk: hold a shortcut on the PC and speak — the same path as a voice
note; the text answer goes to the owner's chat, the spoken answer plays on the PC."""

from __future__ import annotations

import asyncio
import io
import math
import struct
import sys
import wave
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from channels.base import IncomingCallback, IncomingMessage, OutgoingMessage
from core.config.schema import PushToTalkSection
from core.voice import pc_audio
from core.voice.intake import VoiceIntake
from core.voice.ptt import PushToTalk, pc_turn
from tests.integration.test_voice import SETTINGS, Echo, FakeModel, make


def tone(seconds: float, rate: int = pc_audio.RATE) -> bytes:
    n = int(seconds * rate)
    return b"".join(struct.pack("<h", int(8000 * math.sin(i / 8))) for i in range(n))


def test_shortcut_names() -> None:
    assert pc_audio.parse_hotkey("ctrl+alt+space") == (0x3, 0x20, [0x20])
    assert pc_audio.parse_hotkey("Win+F9")[:2] == (0x8, 0x78)
    for bad in ("ctrl+alt", "hyper+space", "ctrl+enterr"):
        with pytest.raises(ValueError):
            pc_audio.parse_hotkey(bad)
    PushToTalkSection(hotkey="shift+f8")
    with pytest.raises(ValidationError):
        PushToTalkSection(hotkey="ctrl+alt+del; rm")


def test_off_unless_turned_on() -> None:
    assert PushToTalkSection().enabled is False


def test_wav_round_trip_and_spoken_answer_decoding() -> None:
    wav = pc_audio.to_wav(tone(1.0))
    with wave.open(io.BytesIO(wav)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getnframes()) == (16000, 1, 16000)
    played = pc_audio.decode_to_wav(wav)              # what winsound gets
    with wave.open(io.BytesIO(played)) as w:
        assert w.getframerate() == 24000 and abs(w.getnframes() - 24000) < 600


class FakeRecorder:
    def __init__(self, pcm: bytes, fail: bool = False) -> None:
        self.pcm, self.fail, self.calls = pcm, fail, []

    def start(self) -> None:
        if self.fail:
            raise pc_audio.AudioError("microphone: no device")
        self.calls.append("start")

    def stop(self) -> bytes:
        self.calls.append("stop")
        return self.pcm


class FakeListener:
    def __init__(self, hotkey: str, down: Any, up: Any, max_seconds: float) -> None:
        self.hotkey, self.down, self.up, self.error = hotkey, down, up, None

    def start(self) -> bool:
        return True

    def stop(self) -> None:
        pass


def _ptt(pcm: bytes, fail: bool = False) -> tuple[PushToTalk, list[tuple[bytes, float]],
                                                    list[str], asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    got: list[tuple[bytes, float]] = []
    cues: list[str] = []

    async def on_audio(wav: bytes, seconds: float) -> None:
        got.append((wav, seconds))
    p = PushToTalk(PushToTalkSection(enabled=True), loop, on_audio,
                   recorder=FakeRecorder(pcm, fail), listener=FakeListener, cue=cues.append)
    return p, got, cues, loop


def test_hold_speak_release_hands_over_one_turn() -> None:
    p, got, cues, loop = _ptt(tone(1.5))
    assert p.start()
    import threading
    t = threading.Thread(target=lambda: (p.down(), p.up()))   # the shortcut's own thread
    t.start()
    t.join()
    loop.run_until_complete(asyncio.wrap_future(p.turns[0], loop=loop))
    loop.close()
    assert cues == ["start", "stop"]
    assert len(got) == 1 and abs(got[0][1] - 1.5) < 0.01
    with wave.open(io.BytesIO(got[0][0])) as w:
        assert w.getframerate() == 16000


def test_a_tap_is_ignored_and_a_missing_microphone_runs_nothing() -> None:
    p, got, _cues, loop = _ptt(tone(0.2))
    p.down()
    p.up()
    assert got == [] and p.turns == []
    p2, got2, cues2, loop2 = _ptt(tone(2.0), fail=True)
    p2.down()
    p2.up()
    assert got2 == [] and cues2 == ["start"] and p2.turns == []   # tone, then no mic
    loop.close()
    loop2.close()


class Here:
    def __init__(self) -> None:
        self.said: list[str] = []

    async def __call__(self, text: str) -> None:
        self.said.append(text)


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


def test_pc_turn_answers_in_chat_and_speaks_on_the_pc(tmp_path: Path) -> None:
    t, _ = make(tmp_path, FakeModel(("পিসির অবস্থা বলো", "bn", -0.05)))
    echo, sent, here = Echo(), [], Here()

    async def send(chat: str, out: OutgoingMessage) -> None:
        sent.append((chat, out))
    vi = VoiceIntake(t, SETTINGS, tmp_path / "audio", echo, send=send)

    async def go() -> None:
        await pc_turn(vi, send, "555", pc_audio.to_wav(tone(1.0)), 1.0, here)
        await _settle()
    asyncio.run(go())
    assert echo.got == ["পিসির অবস্থা বলো"]
    assert len(sent) == 1 and sent[0][0] == "555"                 # text only, no voice note
    assert sent[0][1].text.startswith("💻 PC-তে বলা\n🎙️ শুনলাম") and sent[0][1].voice is None
    assert here.said == ["📥 Task #1 তৈরি হয়েছে — পিসির অবস্থা বলো"]
    assert list((tmp_path / "audio").glob("*")) == []             # the recording is deleted


def test_a_task_started_on_the_pc_is_answered_on_the_pc(tmp_path: Path) -> None:
    t, _ = make(tmp_path, FakeModel(("notepad kholo", "en", -0.05)))
    here = Here()

    async def make_task(msg: IncomingMessage) -> OutgoingMessage:
        return OutgoingMessage("📥 Task #7", task_id=7)
    vi = VoiceIntake(t, SETTINGS, tmp_path / "audio", make_task)

    async def send(chat: str, out: OutgoingMessage) -> None:
        pass
    asyncio.run(pc_turn(vi, send, "555", pc_audio.to_wav(tone(1.0)), 1.0, here))
    assert here.said == []                         # a task's answer comes when it ends
    assert vi.here(7) is here and vi.here(7) is None
    assert vi.finish(7) is not None


def test_unclear_pc_speech_still_waits_for_the_button(tmp_path: Path) -> None:
    t, _ = make(tmp_path, FakeModel(("ফাইল মুছে দাও", "bn", -0.6)))
    here = Here()

    async def make_task(msg: IncomingMessage) -> OutgoingMessage:
        return OutgoingMessage("📥 Task #9", task_id=9)
    vi = VoiceIntake(t, SETTINGS, tmp_path / "audio", make_task)
    sent: list[OutgoingMessage] = []

    async def send(chat: str, out: OutgoingMessage) -> None:
        sent.append(out)

    async def go() -> None:
        await pc_turn(vi, send, "555", pc_audio.to_wav(tone(1.0)), 1.0, here)
        await _settle()
        yes = sent[0].buttons[0][0][1]
        await vi.on_button(IncomingCallback("telegram", "555", "555", "c", yes, "1", "x"))
    asyncio.run(go())
    assert "নিশ্চিত না হওয়া পর্যন্ত কিছু চালাব না" in sent[0].text
    assert here.said and "ঠিক শুনেছি তো" in here.said[0]           # asked aloud on the PC
    assert vi.here(9) is here                        # confirmed task: answered on the PC too


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shortcut")
def test_real_shortcut_registers_and_reports_a_clash() -> None:
    """Registers a shortcut nobody uses and lets it go — no key is pressed."""
    a = pc_audio.HotkeyListener("ctrl+alt+shift+f23", lambda: None, lambda: None)
    b = pc_audio.HotkeyListener("ctrl+alt+shift+f23", lambda: None, lambda: None)
    try:
        assert a.start(), a.error
        assert not b.start() and b.error is not None and "taken" in b.error
    finally:
        a.stop()
        b.stop()
    c = pc_audio.HotkeyListener("ctrl+alt+shift+f23", lambda: None, lambda: None)
    assert c.start(), c.error                         # freed again after stop()
    c.stop()


def test_noise_is_never_read_aloud(tmp_path: Path) -> None:
    """Owner 2026-10-25: a 1.2 s tap came back as made-up words (24%) and Nova read them
    out asking "did you mean …?". Below the floor it just asks again."""
    t, _ = make(tmp_path, FakeModel(("উপিশা, কোলো, ক্ষা, কে, কে, কে, কে", "en", -1.5)))
    echo, here, sent = Echo(), Here(), []

    async def send(chat: str, out: OutgoingMessage) -> None:
        sent.append(out)
    vi = VoiceIntake(t, SETTINGS, tmp_path / "audio", echo)

    async def go() -> None:
        await pc_turn(vi, send, "555", pc_audio.to_wav(tone(1.2)), 1.2, here)
        await _settle()
    asyncio.run(go())
    assert echo.got == [] and sent[0].buttons == []
    assert here.said == ["কিছু বুঝতে পারিনি — আবার একটু স্পষ্ট করে বলবেন?"]
    assert "উপিশা" not in sent[0].text


def test_tone_before_microphone_and_whisper_woken_on_press() -> None:
    events: list[str] = []
    loop = asyncio.new_event_loop()

    class Rec(FakeRecorder):
        def start(self) -> None:
            events.append("mic")

    async def on_audio(wav: bytes, seconds: float) -> None:
        pass

    async def warm() -> None:
        events.append("warm")
    p = PushToTalk(PushToTalkSection(enabled=True), loop, on_audio, recorder=Rec(tone(1)),
                   listener=FakeListener, cue=events.append, on_press=warm)
    p.down()
    loop.run_until_complete(asyncio.wrap_future(p.turns[0], loop=loop))
    loop.close()
    assert events.index("start") < events.index("mic") and "warm" in events
