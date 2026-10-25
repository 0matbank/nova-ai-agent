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
from core.voice.dictionary import SpeechDictionary
from core.voice.speech import asks_to_repeat_in_full, spoken_answer, wants_detail
from core.voice.transcriber import Transcriber
from core.voice.tts import Speaker

_log = get_logger("core")
CALLBACK_PREFIX = "vc"
Downloader = Callable[[str], Awaitable[bytes]]
Handler = Callable[[IncomingMessage], Awaitable[OutgoingMessage | None]]
LANG_NAME = {"bn": "বাংলা", "en": "English"}
# V1.2: a voice task still running after this long gets one short spoken progress line
PROGRESS_AFTER_SECONDS = 8.0
WORKING = {"bn": "কাজ চলছে — শেষ হলে জানাব।", "en": "Working on it — I'll tell you when it's done."}
NEEDS_YOU = {
    True: {"bn": "এই কাজে আপনার অনুমোদন লাগবে — Telegram-এ দেখুন।",
           "en": "This needs your approval — please check Telegram."},
    False: {"bn": "এই কাজে আপনার একটা উত্তর লাগবে — Telegram-এ দেখুন।",
            "en": "I need an answer from you — please check Telegram."}}
REPEATING = "🔊 আগের উত্তরটা পুরো পড়ে শোনাচ্ছি।"


Corrector = Callable[[str, str], Awaitable[str | None]]   # (heard, language) -> suggestion
LowRisk = Callable[[str], bool]
Sender = Callable[[str, OutgoingMessage], Awaitable[object]]
# V1.2 push-to-talk: speak the answer where the owner spoke (the PC), not as a voice note
SayHere = Callable[[str], Awaitable[None]]

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
                 send: Sender | None = None,
                 dictionary: SpeechDictionary | None = None) -> None:
        self.transcriber = transcriber
        # V1.2: the owner's own words and learned fixes, before routing
        self.dictionary = dictionary
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
        # task id -> where its final answer is spoken (a task started on the PC)
        self.said_here: dict[int, SayHere] = {}
        # V1.2: each voice command is a turn; a newer one silences the older answers
        self._turns: dict[str, int] = {}
        # task id -> (chat, turn, what the owner said)
        self.task_turns: dict[int, tuple[str, int, str]] = {}
        # chat -> the last answer that was only summarised aloud ("পুরোটা পড়ে শোনাও")
        self.last_full: dict[str, str] = {}
        self._noticed: set[tuple[int, bool]] = set()
        self.progress_after = PROGRESS_AFTER_SECONDS
        # nonce -> (message, {"y": suggested text, "h": heard text}, expires_at, say here)
        self._pending: dict[str, tuple[IncomingMessage, dict[str, str], float,
                                       SayHere | None, int | None]] = {}

    @property
    def max_chars(self) -> int:
        return int(getattr(getattr(self.speaker, "settings", None), "max_chars", 1200))

    def _new_turn(self, chat_id: str) -> int:
        self._turns[chat_id] = self._turns.get(chat_id, 0) + 1
        return self._turns[chat_id]

    def _current(self, chat_id: str, turn: int | None) -> bool:
        return turn is None or self._turns.get(chat_id) == turn

    def _speech(self, say: str, chat_id: str, request: str) -> str:
        """Smart voice: the whole answer when short or asked for, else its main point."""
        sp = spoken_answer(say, detail=wants_detail(request), said_in=request,
                           max_chars=self.max_chars)
        if not sp.full and chat_id:
            self.last_full[chat_id] = say
        return sp.text

    async def _spoken(self, reply: OutgoingMessage, say: str, chat_id: str = "",
                      here: SayHere | None = None, request: str = "",
                      turn: int | None = None, as_is: bool = False) -> OutgoingMessage:
        if say and not as_is:
            say = self._speech(say, chat_id, request)
        if here is not None:
            if say:
                self._background(self._say_here(here, say, chat_id, turn))
            return reply
        if self.speaker is None or not self.speaker.enabled or not say:
            return reply
        if self.send is not None and chat_id:
            self._background(self._speak_later(chat_id, say, turn))
            return reply
        return dataclasses.replace(reply, voice=await self.speaker.synthesize(say))

    def _background(self, work: Awaitable[None]) -> None:
        job = asyncio.ensure_future(work)
        self._speaking.add(job)
        job.add_done_callback(self._speaking.discard)

    async def _say_here(self, here: SayHere, say: str, chat_id: str = "",
                        turn: int | None = None) -> None:
        if not self._current(chat_id, turn):
            return                               # the owner already said something newer
        try:
            await here(say)
        except Exception:
            _log.exception("spoken reply on the PC failed", extra={"action": "voice.tts"})

    async def _speak_later(self, chat_id: str, say: str, turn: int | None = None) -> None:
        assert self.speaker is not None and self.send is not None
        try:
            audio = await self.speaker.synthesize(say)
            if audio is not None and self._current(chat_id, turn):
                await self.send(chat_id, OutgoingMessage("", voice=audio))
        except Exception:
            _log.exception("spoken reply failed", extra={"action": "voice.tts"})

    def _track(self, reply: OutgoingMessage | None, started: float,
               here: SayHere | None = None, chat_id: str = "", turn: int | None = None,
               request: str = "") -> None:
        if reply is not None and reply.task_id is not None:
            self.voice_tasks[reply.task_id] = started
            if here is not None:
                self.said_here[reply.task_id] = here
            if turn is not None:
                self.task_turns[reply.task_id] = (chat_id, turn, request)
                self._background(self._progress_later(reply.task_id, reply.text, here,
                                                      chat_id, turn, request))

    async def _progress_later(self, task_id: int, receipt: str, here: SayHere | None,
                              chat_id: str, turn: int, request: str) -> None:
        """One short spoken progress line if a voice task is still running — never more."""
        await asyncio.sleep(self.progress_after)
        if task_id not in self.voice_tasks or not self._current(chat_id, turn):
            return
        line = receipt.splitlines()[0] if receipt.startswith("👂") else ""
        lang = "en" if request.isascii() and request.strip() else "bn"
        say = " ".join(x for x in (line, WORKING[lang]) if x)
        await self._spoken(OutgoingMessage(""), say, chat_id, here, turn=turn, as_is=True)

    def task_speech(self, task_id: int, result: str, completed: bool) -> str | None:
        """What to say when a voice task ends — None when a newer voice command came."""
        entry = self.task_turns.pop(task_id, None)
        self._noticed.discard((task_id, True))
        self._noticed.discard((task_id, False))
        chat_id, turn, request = entry if entry is not None else ("", None, "")
        if not self._current(chat_id, turn):
            _log.info(f"voice task #{task_id}: answer not spoken — a newer command came",
                      extra={"action": "voice.tts", "status": "skipped"})
            return None
        sp = spoken_answer(result, completed=completed, detail=wants_detail(request),
                           said_in=request, max_chars=self.max_chars)
        if completed and not sp.full and chat_id:
            self.last_full[chat_id] = result
        return sp.text

    def notice_speech(self, task_id: int, approval: bool) -> str | None:
        """A voice task now needs the owner: say it once, briefly."""
        entry = self.task_turns.get(task_id)
        if entry is None or not self._current(entry[0], entry[1]):
            return None
        if (task_id, approval) in self._noticed:
            return None
        self._noticed.add((task_id, approval))
        lang = "en" if entry[2].isascii() and entry[2].strip() else "bn"
        return NEEDS_YOU[approval][lang]

    def here(self, task_id: int) -> SayHere | None:
        """Where a voice task's final answer is spoken, when not as a voice note."""
        return self.said_here.pop(task_id, None)

    def finish(self, task_id: int) -> float | None:
        """A voice task ended: log its whole time (voice note → final answer)."""
        started = self.voice_tasks.pop(task_id, None)
        if started is not None:
            _log.info(f"voice task #{task_id} done {time.time() - started:.1f}s after the "
                      "voice note arrived", extra={"action": "voice.e2e", "status": "ok",
                                                   "task_id": task_id})
        return started

    async def handle(self, msg: IncomingMessage, download: Downloader,
                     here: SayHere | None = None) -> OutgoingMessage:
        assert msg.voice is not None
        if msg.voice.duration > self.settings.max_duration_seconds:
            return OutgoingMessage(f"🎙️ Voice অনেক লম্বা ({msg.voice.duration}s) — সর্বোচ্চ "
                                   f"{self.settings.max_duration_seconds}s।")
        turn = self._new_turn(msg.chat_id)
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        suffix = ".wav" if msg.voice.mime_type == "audio/wav" else ".ogg"
        path = self.audio_dir / f"voice-{stamp}{suffix}"
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
        if not t.text or t.confidence < self.settings.not_understood_below:
            ask = "কিছু বুঝতে পারিনি — আবার একটু স্পষ্ট করে বলবেন?"
            if t.text:
                _log.info(f"voice not understood (conf {t.confidence:.2f}) — asked again",
                          extra={"action": "voice.transcribe", "status": "unclear"})
            return await self._spoken(OutgoingMessage(f"🎙️ {ask}"), ask, msg.chat_id, here,
                                      turn=turn, as_is=True)

        heard = (f"🎙️ শুনলাম ({LANG_NAME.get(t.language, t.language)}, "
                 f"{t.confidence:.0%}): «{t.text}»")
        text = t.text
        if self.dictionary is not None:
            fixed = self.dictionary.apply(text)
            if fixed.changes:
                text = fixed.text
                heard += f"\n📖 অভিধানে ঠিক করলাম: «{text}»"
                _log.info(f"speech dictionary: {len(fixed.changes)} fix(es) applied",
                          extra={"action": "voice.dictionary", "status": "ok"})
        last = self.last_full.get(msg.chat_id)
        if last is not None and asks_to_repeat_in_full(text):
            # V1.2: "পুরোটা পড়ে শোনাও" — the last answer in full, not a new task
            full = spoken_answer(last, detail=True, said_in=text, max_chars=self.max_chars)
            return await self._spoken(OutgoingMessage(f"{heard}\n\n{REPEATING}"), full.text,
                                      msg.chat_id, here, turn=turn, as_is=True)
        text_msg = dataclasses.replace(msg, text=text, voice=None)
        safe = is_read_only_request(text) or (self.low_risk is not None
                                              and self.low_risk(text))
        threshold = (self.settings.confirm_below_confidence_safe if safe
                     else self.settings.confirm_below_confidence)
        if t.confidence < threshold:
            return await self._ask_to_confirm(text_msg, text, t.language, heard, here, turn)
        r0 = time.perf_counter()
        reply = await self.handle_text(text_msg)
        self._track(reply, started, here, msg.chat_id, turn, text)
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
            out, reply.text if reply else "", msg.chat_id, here, request=text, turn=turn)

    async def _ask_to_confirm(self, msg: IncomingMessage, text: str, language: str,
                              heard: str, here: SayHere | None = None,
                              turn: int | None = None) -> OutgoingMessage:
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
        self._pending[nonce] = (msg, choices, time.time() + self.settings.confirm_timeout_seconds,
                                here, turn)
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
        return await self._spoken(OutgoingMessage(body, buttons), say, msg.chat_id, here,
                                  turn=turn, as_is=True)

    def _expire(self) -> None:
        now = time.time()
        for k in [k for k, (_, _c, exp, _h, _t) in self._pending.items() if exp < now]:
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
        msg, choices, _, here, turn = entry
        if msg.chat_id != cb.chat_id:
            return CallbackReply("Not allowed")
        if parts[2] not in choices:
            return CallbackReply("🔁 ঠিক আছে",
                                 f"{cb.message_text}\n\n🔁 বাতিল — আবার বলুন বা লিখে দিন।")
        chosen = choices[parts[2]]
        if parts[2] == "y" and self.dictionary is not None and chosen != choices["h"]:
            # V1.2: the owner confirmed a corrected reading — count its small changes
            # (the second confirmation of the same change is learned)
            self.dictionary.observe(choices["h"], chosen)
        reply = await self.handle_text(dataclasses.replace(msg, text=chosen))
        self._track(reply, time.time(), here, msg.chat_id, turn, chosen)
        result = reply.text if reply else "✅"
        return CallbackReply("✅ চালানো হচ্ছে",
                             f"{cb.message_text}\n\n✅ নিশ্চিত: «{chosen}»\n{result}")
