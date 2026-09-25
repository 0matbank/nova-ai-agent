"""Voice message → transcript → the same path as typed text (plan §7).

Unclear transcription never acts: below `confirm_below_confidence` the owner
sees «what was heard» with ✅/❌ and nothing runs until ✅ (plan §7).
"""

from __future__ import annotations

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

READ_ONLY_INTENTS = frozenset({"pc_status", "screenshot", "processes"})
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
                 speaker: Speaker | None = None) -> None:
        self.transcriber = transcriber
        self.settings = settings
        self.audio_dir = audio_dir
        self.handle_text = handle_text
        self.corrector = corrector
        self.speaker = speaker
        # Tasks started by voice: their final answer is spoken too (voice mode).
        self.voice_tasks: set[int] = set()
        # nonce -> (message, {"y": suggested text, "h": heard text}, expires_at)
        self._pending: dict[str, tuple[IncomingMessage, dict[str, str], float]] = {}

    async def _spoken(self, reply: OutgoingMessage, say: str) -> OutgoingMessage:
        if self.speaker is None or not self.speaker.enabled:
            return reply
        return dataclasses.replace(reply, voice=await self.speaker.synthesize(say))

    def _track(self, reply: OutgoingMessage | None) -> None:
        if reply is not None and reply.task_id is not None:
            self.voice_tasks.add(reply.task_id)

    async def handle(self, msg: IncomingMessage, download: Downloader) -> OutgoingMessage:
        assert msg.voice is not None
        if msg.voice.duration > self.settings.max_duration_seconds:
            return OutgoingMessage(f"🎙️ Voice অনেক লম্বা ({msg.voice.duration}s) — সর্বোচ্চ "
                                   f"{self.settings.max_duration_seconds}s।")
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        path = self.audio_dir / f"voice-{stamp}.ogg"
        try:
            path.write_bytes(await download(msg.voice.file_id))
            t = await self.transcriber.transcribe(path)
        except FileNotFoundError as e:
            return OutgoingMessage(f"🎙️ Voice model পাওয়া যায়নি: {e}")
        finally:
            if not self.settings.keep_audio:
                path.unlink(missing_ok=True)
        _log.info(f"voice transcribed: lang={t.language} conf={t.confidence:.2f} "
                  f"{t.duration:.1f}s audio in {t.seconds:.1f}s on {t.device}",
                  extra={"action": "voice.transcribe", "status": "ok"})
        if not t.text:
            ask = "কিছু বুঝতে পারিনি — আবার একটু স্পষ্ট করে বলবেন?"
            return await self._spoken(OutgoingMessage(f"🎙️ {ask}"), ask)

        heard = (f"🎙️ শুনলাম ({LANG_NAME.get(t.language, t.language)}, "
                 f"{t.confidence:.0%}): «{t.text}»")
        text_msg = dataclasses.replace(msg, text=t.text, voice=None)
        threshold = (self.settings.confirm_below_confidence_safe if is_read_only_request(t.text)
                     else self.settings.confirm_below_confidence)
        if t.confidence < threshold:
            return await self._ask_to_confirm(text_msg, t.text, t.language, heard)
        reply = await self.handle_text(text_msg)
        self._track(reply)
        out = OutgoingMessage(f"{heard}\n\n{reply.text}" if reply else heard,
                              reply.buttons if reply else [], reply.photo if reply else None,
                              task_id=reply.task_id if reply else None)
        # A task's answer is spoken when it completes; a direct reply is spoken now.
        return out if out.task_id is not None else await self._spoken(out, reply.text
                                                                      if reply else "")

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
        return await self._spoken(OutgoingMessage(body, buttons), say)

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
        self._track(reply)
        result = reply.text if reply else "✅"
        return CallbackReply("✅ চালানো হচ্ছে",
                             f"{cb.message_text}\n\n✅ নিশ্চিত: «{chosen}»\n{result}")
