"""V1.2 Smart Voice Response: what Nova SAYS is not what it WRITES.

The full result always goes to the chat unchanged; the voice gets:
- a short answer in full ("রাজধানী ঢাকা।"),
- a long or structured result (a report, PC status, research, code, a list) as its
  main point in 1–2 sentences plus "the details are in Telegram",
- everything (up to the speaking limit) when the owner asked for it — in the request
  ("বিস্তারিত বলো") or right after ("পুরোটা পড়ে শোনাও"),
- never "done" for a task that did not complete, never a password, key or card number.

Decided from the shape of the text and what the owner asked — no AI call, no per-command
rule (owner 2026-10-25).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.voice.tts import language_of, speakable

SHORT_CHARS = 220          # spoken in full when the answer is about this long or shorter
SHORT_LINES = 3
SUMMARY_CHARS = 200        # the spoken main point of a long result
MAX_SENTENCES = 2
# "পুরোটা পড়ে শোনাও", "বিস্তারিত বলো", "puro ta bolo", "read it all", "in detail"
_DETAIL = re.compile(
    r"(পুরোটা|পুরো\s*(?:টা|লেখা|ফল|রিপোর্ট)|সবটা|সব\s*কিছু|বিস্তারিত|বিস্তারিতভাবে|"
    r"\b(?:puro\s*ta|purota|puro|sobta|sob\s*kichu|bistarito|details?|detailed|full|"
    r"whole|everything|entire)\b)\s*"
    r"(?:\S+\s+){0,2}?(পড়ে\s*)?(শোনাও|শুনাও|বলো|বল|পড়ো|পড়|\b(?:bolo|bol|shonao|sunao|"
    r"shunao|poro|read|say|tell)\b)|\bread\s+(?:it\s+|that\s+|the\s+\w+\s+)?(?:all|out|in\s+full)"
    r"\b|\bin\s+(?:full\s+)?detail\b|\btell\s+me\s+(?:the\s+)?details\b",
    re.IGNORECASE)
# with the detail words gone, only these left: "পুরোটা আবার একটু পড়ে শোনাও" is about
# the last answer; "পিসির অবস্থা বিস্তারিত বলো" is a new request
_FILLER = re.compile(r"\b(?:abar|ektu|amake|please|pls|plz|again|it|that|this|me|nova|"
                     r"ta|ti|to|ki|na)\b|আবার|একটু|আমাকে|প্লিজ|নোভা|টা|টি|তো|[,.!?।]",
                     re.IGNORECASE)
# "CPU: 15%" "Disk free: 82/109 GB" — a list of facts, not prose
_KEY_VALUE = re.compile(r"^\W{0,3}(?P<k>[^:：\n]{1,32}?)\s*[:：]\s*(?P<v>\S.*)$")
_SENTENCE = re.compile(r"(?<=[।.!?])\s+")
_CODE = re.compile(r"```.*?```", re.DOTALL)
_DONE_WORDS = re.compile(
    r"\b(?:completed?|done|finished|success(?:fully)?)\b|সম্পন্ন|শেষ\s*হয়েছে|সফল(?:ভাবে)?",
    re.IGNORECASE)

TELEGRAM_NOTE = {"bn": "বিস্তারিত Telegram-এ দিয়েছি।", "en": "The details are in Telegram."}
REST_NOTE = {"bn": "বাকিটা Telegram-এ আছে।", "en": "The rest is in Telegram."}
NOT_DONE = {"bn": "কাজটা শেষ করা যায়নি।", "en": "The task could not be finished."}
HIDDEN = {"bn": "গোপন তথ্য", "en": "a hidden value"}


@dataclass(frozen=True)
class Speech:
    text: str                 # what to say
    full: bool                # the whole answer was spoken (nothing left for "read it all")
    hidden: bool = False      # a secret was left out


def wants_detail(text: str) -> bool:
    return bool(_DETAIL.search(text))


def asks_to_repeat_in_full(text: str) -> bool:
    """Only "read it all / say it in detail" — about the last answer, not a new task."""
    if not wants_detail(text):
        return False
    return not _FILLER.sub(" ", _DETAIL.sub(" ", text)).split()


def hide_secrets(text: str) -> tuple[str, bool]:
    """Passwords/PINs the text itself states, key/token shapes, card numbers → a word."""
    from core.orchestrator.computer import mask
    masked = mask(text, text)
    if masked == text:
        return text, False
    word = HIDDEN[language_of(text)]
    return masked.replace("•••", word), True


def _lines(text: str) -> list[str]:
    bare = (ln.strip().lstrip("•·-*– ").strip() for ln in _CODE.sub("\n", text).splitlines())
    return [ln for ln in bare if ln]


def _facts(lines: list[str]) -> list[tuple[str, str]]:
    out = []
    for ln in lines:
        m = _KEY_VALUE.match(ln)
        if m:
            value = re.split(r"\s*[(,—–]\s*", m["v"], maxsplit=1)[0].strip()
            out.append((m["k"].strip(), value.rstrip(".।")))
    return out


def _main_point(text: str, lang: str) -> str:
    """The first one or two sentences of the prose; for a report of facts, its first two
    facts. Code is never part of it."""
    lines = _lines(text)
    facts = _facts(lines)
    prose_lines = [ln for ln in lines if not _KEY_VALUE.match(ln)]
    if facts and (not prose_lines or (len(facts) >= 3 and len(facts) * 2 >= len(lines))):
        # a report of facts: its first two, the way a person would say them
        return ", ".join(f"{k} {v}" for k, v in facts[:2])
    prose = speakable("\n".join(prose_lines or lines), max_chars=2000)
    said: list[str] = []
    for sentence in _SENTENCE.split(prose):
        if said and (len(" ".join(said)) + len(sentence) > SUMMARY_CHARS
                     or len(said) >= MAX_SENTENCES):
            break
        said.append(sentence)
    return " ".join(said)[:SUMMARY_CHARS].strip()


def _end(text: str, lang: str) -> str:
    text = text.strip()
    return text if text.endswith(("।", ".", "?", "!")) else text + ("।" if lang == "bn" else ".")


def spoken_answer(result: str, *, completed: bool | None = None, detail: bool = False,
                  max_chars: int = 1200, said_in: str = "") -> Speech:
    """What to say for `result`. `completed` is the task's real end state (None: not a
    task, e.g. a chat answer) — when False, `result` is the reason it failed; `detail`:
    the owner asked to hear all of it; `said_in`: the owner's words, whose language the
    added notes follow (a PC status is written in English, the owner spoke Bangla)."""
    lang = language_of(said_in or result) if (said_in or result).strip() else "bn"
    safe, hidden = hide_secrets(result)
    if completed is False:
        reason = _main_point(_DONE_WORDS.sub("", safe), lang) if safe.strip() else ""
        if len(reason.split()) < 3:
            reason = ""                       # a fragment explains nothing
        say = NOT_DONE[lang] + (f" {_end(reason, lang)}" if reason else "")
        return Speech(speakable(say, max_chars), full=True, hidden=hidden)
    whole = speakable(safe, max_chars=max_chars)
    lines = _lines(safe)
    short = (len(whole) <= SHORT_CHARS and len(lines) <= SHORT_LINES
             and not _CODE.search(safe) and len(_facts(lines)) < 3)
    if short:
        return Speech(whole, full=True, hidden=hidden)
    if detail:
        cut = len(speakable(safe, max_chars=100_000)) > len(whole)
        return Speech(f"{whole} {REST_NOTE[lang]}" if cut else whole, full=not cut,
                      hidden=hidden)
    point = _main_point(safe, lang)
    return Speech(f"{_end(point, lang)} {TELEGRAM_NOTE[lang]}", full=False, hidden=hidden)
