"""V1.2 learning from the owner's own fixes after dictation (Atticus item 3, adapted).

After Nova types dictated words, the owner may fix a misheard word right there. For a
short while the Desktop Worker reads only those words again (found by the text around
them); once the owner's edit has settled — or the field moves on, e.g. the message was
sent — a small change that sounds like a mishearing goes to the Speech Dictionary
(`observe`: the same fix seen twice is learned). The owner is told what was learned.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from core.log import get_logger
from core.voice.dictionary import EDITED, SpeechDictionary, sounds_alike

_log = get_logger("core")

POLL_SECONDS = 0.5
SETTLED_SECONDS = 2.0                 # an edit unchanged this long is finished
WATCH_SECONDS = 25.0
LEARNED = ("📖 আপনার সংশোধন থেকে শিখলাম: কণ্ঠে «{heard}» শুনলে «{meant}» লিখব।\n"
           "ভুল হলে লিখুন: «অভিধান থেকে {heard} মুছো»")
# the first time: the owner sees the fix was noticed (owner 2026-10-26: «কোনো বার্তা
# আসেনি» after one fix — a fix is learned only when seen twice)
NOTICED = "📝 আপনার সংশোধন দেখলাম: {pairs}। আরেকবার একই ভুল ঠিক করলে শিখে নেব।"

# the words now where the dictated ones were: (span, why it ended — None while watching)
Peek = Callable[[str], Awaitable[tuple[str | None, str | None]]]
Tell = Callable[[str, str], Awaitable[object]]


async def edited_words(peek: Peek, token: str, typed: str,
                       seconds: float = WATCH_SECONDS, poll: float = POLL_SECONDS,
                       settled: float = SETTLED_SECONDS) -> str | None:
    """The owner's fixed version of `typed`, or None when it was left as it was."""
    last, changed = typed, time.monotonic()
    until = changed + seconds
    while time.monotonic() < until:
        await asyncio.sleep(poll)
        span, ended = await peek(token)
        if span is None:
            # the field moved on (the message was sent, another field): keep the last fix
            _log.info(f"dictation watch ended: {ended}", extra={"action": "voice.learn"})
            break
        if span != last:
            last, changed = span, time.monotonic()
        elif last != typed and time.monotonic() - changed >= settled:
            break
    return last if last.strip() and last != typed else None


class EditLearner:
    def __init__(self, dictionary: SpeechDictionary, peek: Peek, tell: Tell,
                 poll: float = POLL_SECONDS, settled: float = SETTLED_SECONDS) -> None:
        self.dictionary = dictionary
        self.peek = peek
        self.tell = tell
        self.poll = poll
        self.settled = settled
        self._jobs: set[asyncio.Task[list[tuple[str, str]]]] = set()

    def after_skill(self, chat_id: str, skill: tuple[str, str], params: dict[str, Any],
                    data: dict[str, Any], ok: bool) -> None:
        """Executor hook: a dictation that landed in a field Nova can read back."""
        token = data.get("watch") if ok and skill == ("desktop-ui", "dictate") else None
        if not token or not chat_id:
            return
        job = asyncio.ensure_future(self.learn(chat_id, str(params.get("text", "")), token))
        self._jobs.add(job)
        job.add_done_callback(self._jobs.discard)

    async def learn(self, chat_id: str, typed: str, token: str) -> list[tuple[str, str]]:
        try:
            edited = await edited_words(self.peek, token, typed, poll=self.poll,
                                        settled=self.settled)
            if edited is None:
                _log.info("dictation watch: the words were left as typed",
                          extra={"action": "voice.learn", "status": "ok"})
                return []
            noticed: list[tuple[str, str]] = []
            learned = self.dictionary.observe(typed, edited, alike=sounds_alike,
                                              source=EDITED, many=True, noticed=noticed)
            _log.info(f"dictation edit seen: {len(learned)} fix(es) learned, "
                      f"{len(noticed)} noticed for the first time",
                      extra={"action": "voice.learn", "status": "ok"})
            for heard, meant in learned:
                await self.tell(chat_id, LEARNED.format(heard=heard, meant=meant))
            if noticed:
                await self.tell(chat_id, NOTICED.format(
                    pairs=", ".join(f"«{h}» → «{m}»" for h, m in noticed)))
            return learned
        except Exception:  # noqa: BLE001 - learning is a help, never a failure to show
            _log.exception("learning from a dictation edit failed",
                           extra={"action": "voice.learn"})
            return []
