"""V1.2 push-to-talk: hold a shortcut on the PC and speak. What was said takes the same
path as a Telegram voice note (dictionary, confirmation, approvals); the text answer
goes to the owner's chat, the spoken answer plays on the PC."""

from __future__ import annotations

import asyncio
import dataclasses
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from channels.base import IncomingMessage, OutgoingMessage, VoiceRef
from core.config.schema import PushToTalkSection
from core.log import get_logger
from core.voice import pc_audio
from core.voice.intake import SayHere

_log = get_logger("core")

OnAudio = Callable[[bytes, float], Awaitable[None]]     # (WAV bytes, seconds)


class Recorder(Protocol):
    def start(self) -> None: ...
    def stop(self) -> bytes: ...


class Listener(Protocol):
    error: str | None
    def start(self) -> bool: ...
    def stop(self) -> None: ...


class PushToTalk:
    def __init__(self, settings: PushToTalkSection, loop: asyncio.AbstractEventLoop,
                 on_audio: OnAudio, recorder: Recorder | None = None,
                 listener: Callable[..., Listener] | None = None,
                 cue: Callable[[str], None] | None = None,
                 on_press: Callable[[], Awaitable[object]] | None = None) -> None:
        self.settings = settings
        self.loop = loop
        self.on_audio = on_audio
        self.recorder: Recorder = recorder or pc_audio.WaveInRecorder(settings.max_seconds)
        self.cue = cue if cue is not None else (pc_audio.cue if settings.cue else None)
        make = listener or pc_audio.HotkeyListener
        self.listener: Listener = make(settings.hotkey, self.down, self.up,
                                       settings.max_seconds)
        # the moment the key goes down: wake Whisper while the owner is still speaking
        self.on_press = on_press
        self.recording = False
        self.turns: list[Any] = []        # futures of the turns sent to the event loop

    def start(self) -> bool:
        ok = self.listener.start()
        if ok:
            _log.info(f"push-to-talk ready: hold {self.settings.hotkey} and speak",
                      extra={"action": "voice.ptt", "status": "ok"})
        else:
            _log.warning(f"push-to-talk off: {self.listener.error}",
                         extra={"action": "voice.ptt", "status": "error"})
        return ok

    def stop(self) -> None:
        self.listener.stop()
        if self.recording:
            self.recorder.stop()
            self.recording = False

    # called on the shortcut's own thread
    def down(self) -> None:
        if self.on_press is not None:
            self.turns.append(asyncio.run_coroutine_threadsafe(self._warm(), self.loop))
        if self.cue is not None:
            self.cue("start")              # before the microphone opens: not in the recording
        try:
            self.recorder.start()
        except pc_audio.AudioError as e:
            _log.warning(f"microphone not available: {e}",
                         extra={"action": "voice.ptt", "status": "error"})
            return
        self.recording = True

    async def _warm(self) -> None:
        assert self.on_press is not None
        try:
            await self.on_press()
        except Exception:
            _log.exception("push-to-talk warm-up failed", extra={"action": "voice.ptt"})

    def up(self) -> None:
        if not self.recording:
            return
        pcm = self.recorder.stop()
        self.recording = False
        if self.cue is not None:
            self.cue("stop")
        seconds = len(pcm) / (pc_audio.RATE * 2)
        if seconds < self.settings.min_seconds:
            _log.info(f"push-to-talk: {seconds:.1f}s is too short — ignored",
                      extra={"action": "voice.ptt", "status": "ignored"})
            return
        _log.info(f"push-to-talk: {seconds:.1f}s heard",
                  extra={"action": "voice.ptt", "status": "ok"})
        wav = pc_audio.to_wav(pcm)
        self.turns.append(asyncio.run_coroutine_threadsafe(self._turn(wav, seconds),
                                                           self.loop))

    async def _turn(self, wav: bytes, seconds: float) -> None:
        t0 = time.perf_counter()
        try:
            await self.on_audio(wav, seconds)
        except Exception:
            _log.exception("push-to-talk turn failed", extra={"action": "voice.ptt"})
        else:
            _log.info(f"push-to-talk turn handed off in {time.perf_counter() - t0:.1f}s",
                      extra={"action": "voice.ptt", "status": "ok"})


async def pc_turn(voice: Any, send: Callable[[str, OutgoingMessage], Awaitable[object]],
                  owner_chat: str, wav: bytes, seconds: float,
                  here: SayHere | None) -> OutgoingMessage:
    """One spoken turn from the PC microphone, as if it were the owner's voice note."""
    msg = IncomingMessage("telegram", owner_chat, owner_chat, "", "", datetime.now(UTC),
                          VoiceRef("pc-mic", max(1, round(seconds)), "audio/wav", len(wav)))

    async def recorded(_file_id: str) -> bytes:
        return wav
    out: OutgoingMessage = await voice.handle(msg, recorded, here=here)
    out = dataclasses.replace(out, text=f"💻 PC-তে বলা\n{out.text}")
    await send(owner_chat, out)
    return out


def speak_on_pc(speaker: Any) -> SayHere:
    async def say(text: str) -> None:
        audio = await speaker.synthesize(text)
        if audio is not None:
            await asyncio.to_thread(pc_audio.play, audio)
    return say
