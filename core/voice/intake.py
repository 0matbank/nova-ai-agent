"""Voice message → transcript → the same path as typed text (plan §7).

Unclear transcription never acts: below `confirm_below_confidence` the owner
sees «what was heard» with ✅/❌ and nothing runs until ✅ (plan §7).
"""

from __future__ import annotations

import asyncio
import dataclasses
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from channels.base import CallbackReply, IncomingCallback, IncomingMessage, OutgoingMessage
from core.config.schema import VoiceSection
from core.log import get_logger
from core.router.intent import classify_by_rules
from core.voice.transcriber import Transcriber
from core.voice.tts import Speaker

_log = get_logger("core")
CALLBACK_PREFIX = "vc"
Downloader = Callable[[str], Awaitable[bytes]]
Handler = Callable[[IncomingMessage], Awaitable[OutgoingMessage | None]]
LANG_NAME = {"bn": "বাংলা", "en": "English"}


Corrector = Callable[[str, str], Awaitable[str | None]]   # (heard, language) -> suggestion
LowRisk = Callable[[str], bool]
Sender = Callable[[str, OutgoingMessage], Awaitable[object]]

READ_ONLY_INTENTS = frozenset({"pc_status", "screenshot", "processes", "open_windows"})
# \b does not work inside Bengali script: require no Bengali letter on either side,
# so "কি" matches the question word but not the start of "কিছু".
_BN = r"[ঀ-৿]"
_QUESTION = re.compile(r"\?|\b(what|who|where|when|why|how|which)\b|"
                       rf"(?<!{_BN})(কি|কী|কে|কোথায়|কখন|কেন|কীভাবে|কত|কোনটা|কোনটি)(?!{_BN})",
                       re.IGNORECASE)


def is_read_only_request(text: str) -> bool:
    """Misheard read-only requests cannot do harm; anything else (unknown or an
    action) keeps the strict threshold (plan §7)."""
    hit = classify_by_rules(text)
    if hit is not None:
        return hit.category in READ_ONLY_INTENTS
    return bool(_QUESTION.search(text))


class VoiceIntake:
    def __init__(self, transcriber: Transcriber, settings: VoiceSection, audio_dir: Path,
                 handle_text: Handler, corrector: Corrector | None = None,
                 speaker: Speaker | None = None, low_risk: LowRisk | None = None,
                 send: Sender | None = None) -> None:
        self.transcriber = transcriber
        # V1.1: opening a clearly named installed app is harmless like a read-only ask
        self.low_risk = low_risk
        # V1.1: when set, a spoken reply follows the text instead of holding it back
        self.send = send
        self._speaking: set[asyncio.Task[None]] = set()
        self.settings = settings
        self.audio_dir = audio_dir
        self.handle_text = handle_text
        self.corrector = corrector
        self.speaker = speaker
        # Tasks started by voice: their final answer is spoken too (voice mode).
        # task id -> when its voice note arrived (end-to-end time is logged at the end)
        self.voice_tasks: dict[int, float] = {}
        # nonce -> (message, {"y": suggested text, "h": heard text}, expires_at)
        self._pending: dict[str, tuple[IncomingMessage, dict[str, str], float]] = {}

    async def _spoken(self, reply: OutgoingMessage, say: str,
                      chat_id: str = "") -> OutgoingMessage:
        if self.speaker is None or not self.speaker.enabled or not say:
            return reply
        if self.send is not None and chat_id:
            job = asyncio.create_task(self._speak_later(chat_id, say))
            self._speaking.add(job)
            job.add_done_callback(self._speaking.discard)
            return reply
        return dataclasses.replace(reply, voice=await self.speaker.synthesize(say))

    async def _speak_later(self, chat_id: str, say: str) -> None:
        assert self.speaker is not None and self.send is not None
        try:
            audio = await self.speaker.synthesize(say)
            if audio is not None:
                await self.send(chat_id, OutgoingMessage("", voice=audio))
        except Exception:
            _log.exception("spoken reply failed", extra={"action": "voice.tts"})

    def _track(self, reply: OutgoingMessage | None, started: float) -> None:
        if reply is not None and reply.task_id is not None:
            self.voice_tasks[reply.task_id] = started

    def finish(self, task_id: int) -> float | None:
        """A voice task ended: log its whole time (voice note → final answer)."""
        started = self.voice_tasks.pop(task_id, None)
        if started is not None:
            _log.info(f"voice task #{task_id} done {time.time() - started:.1f}s after the "
                      "voice note arrived", extra={"action": "voice.e2e", "status": "ok",
                                                   "task_id": task_id})
        return started

    async def handle(self, msg: IncomingMessage, download: Downloader) -> OutgoingMessage:
        assert msg.voice is not None
        if msg.voice.duration > self.settings.max_duration_seconds:
            return OutgoingMessage(f"🎙️ Voice অনেক লম্বা ({msg.voice.duration}s) — সর্বোচ্চ "
                                   f"{self.settings.max_duration_seconds}s।")
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        path = self.audio_dir / f"voice-{stamp}.ogg"
        started, t0 = time.time(), time.perf_counter()
        # V1.1: a cold Whisper load runs while the voice note downloads
        warm = asyncio.create_task(self.transcriber.warm())
        warm.add_done_callback(lambda f: f.cancelled() or f.exception())
        try:
            path.write_bytes(await download(msg.voice.file_id))
            t_download = time.perf_counter() - t0
            await warm
            t = await self.transcriber.transcribe(path)
        except FileNotFoundError as e:
            return OutgoingMessage(f"🎙️ Voice model পাওয়া যায়নি: {e}")
        finally:
            if not self.settings.keep_audio:
                path.unlink(missing_ok=True)
        load = self.transcriber.last_load_seconds
        _log.info(f"voice transcribed: lang={t.language} conf={t.confidence:.2f} "
                  f"{t.duration:.1f}s audio in {t.seconds:.1f}s on {t.device}",
                  extra={"action": "voice.transcribe", "status": "ok"})
        if not t.text:
            ask = "কিছু বুঝতে পারিনি — আবার একটু স্পষ্ট করে বলবেন?"
            return await self._spoken(OutgoingMessage(f"🎙️ {ask}"), ask, msg.chat_id)

        heard = (f"🎙️ শুনলাম ({LANG_NAME.get(t.language, t.language)}, "
                 f"{t.confidence:.0%}): «{t.text}»")
        text_msg = dataclasses.replace(msg, text=t.text, voice=None)
        safe = is_read_only_request(t.text) or (self.low_risk is not None
                                                and self.low_risk(t.text))
        threshold = (self.settings.confirm_below_confidence_safe if safe
                     else self.settings.confirm_below_confidence)
        if t.confidence < threshold:
            return await self._ask_to_confirm(text_msg, t.text, t.language, heard)
        r0 = time.perf_counter()
        reply = await self.handle_text(text_msg)
        self._track(reply, started)
        _log.info(f"voice timing: download {t_download:.2f}s, whisper load {load:.1f}s, "
                  f"transcribe {t.seconds:.2f}s, hand-off {time.perf_counter() - r0:.2f}s,"
                  f" total {time.perf_counter() - t0:.2f}s",
                  extra={"action": "voice.timing", "status": "ok",
                         "task_id": reply.task_id if reply else None})
        out = OutgoingMessage(f"{heard}\n\n{reply.text}" if reply else heard,
                              reply.buttons if reply else [], reply.photo if reply else None,
                              task_id=reply.task_id if reply else None)
        # A task's answer is spoken when it completes; a direct reply is spoken now.
        return out if out.task_id is not None else await self._spoken(
            out, reply.text if reply else "", msg.chat_id)

    async def _ask_to_confirm(self, msg: IncomingMessage, text: str, language: str,
                              heard: str) -> OutgoingMessage:
        """Never guess: show what was heard (and a suggested reading), run nothing
        until the owner taps ✅ (plan §7)."""
        suggestion = None
        if self.corrector is not None:
            try:
                suggestion = await self.corrector(text, language)
            except Exception:
                _log.exception("voice correction failed", extra={"action": "voice.correct"})
        self._expire()
        nonce = secrets.token_urlsafe(9)
        choices = {"y": suggestion or text, "h": text}
        self._pending[nonce] = (msg, choices, time.time() + self.settings.confirm_timeout_seconds)
        base = f"{CALLBACK_PREFIX}:{nonce}"
        if suggestion and suggestion.strip() != text.strip():
            body = (f"{heard}\n\n🤔 পুরোপুরি নিশ্চিত নই। আপনি কি এটা বলতে চেয়েছেন?\n"
                    f"«{suggestion}»\n\nনিশ্চিত না হওয়া পর্যন্ত কিছু চালাব না।")
            buttons = [[("✅ হ্যাঁ, এটাই", f"{base}:y")],
                       [("✏️ যা শুনেছ সেটাই", f"{base}:h"), ("🔁 আবার বলব", f"{base}:n")]]
            say = f"পুরোপুরি নিশ্চিত নই। আপনি কি বলতে চেয়েছেন: {suggestion}?"
        else:
            body = f"{heard}\n\nঠিক শুনেছি তো? নিশ্চিত না হওয়া পর্যন্ত কিছু চালাব না।"
            buttons = [[("✅ ঠিক আছে, চালাও", f"{base}:y"), ("🔁 আবার বলব", f"{base}:n")]]
            say = f"ঠিক শুনেছি তো? আপনি বলেছেন: {text}?"
        return await self._spoken(OutgoingMessage(body, buttons), say, msg.chat_id)

    def _expire(self) -> None:
        now = time.time()
        for k in [k for k, (_, _c, exp) in self._pending.items() if exp < now]:
            del self._pending[k]

    async def on_button(self, cb: IncomingCallback) -> CallbackReply:
        parts = cb.data.split(":")
        if len(parts) != 3 or parts[0] != CALLBACK_PREFIX:
            return CallbackReply("Unknown button")
        self._expire()
        entry = self._pending.pop(parts[1], None)
        if entry is None:
            return CallbackReply("সময় শেষ বা আগেই সিদ্ধান্ত হয়েছে",
                                 f"{cb.message_text}\n\n⌛ বাতিল")
        msg, choices, _ = entry
        if msg.chat_id != cb.chat_id:
            return CallbackReply("Not allowed")
        if parts[2] not in choices:
            return CallbackReply("🔁 ঠিক আছে",
                                 f"{cb.message_text}\n\n🔁 বাতিল — আবার বলুন বা লিখে দিন।")
        chosen = choices[parts[2]]
        reply = await self.handle_text(dataclasses.replace(msg, text=chosen))
        self._track(reply, time.time())
        result = reply.text if reply else "✅"
        return CallbackReply("✅ চালানো হচ্ছে",
                             f"{cb.message_text}\n\n✅ নিশ্চিত: «{chosen}»\n{result}")
