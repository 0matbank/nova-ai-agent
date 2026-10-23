"""Spoken / typed app name → an installed app (V1.1).

Order: exact name → normalized name → alias (curated, learned from the owner) →
sound-alike (fuzzy) → [AI fallback, in core/router/intent.py]. Unclear → Nova asks
with a short numbered list instead of saying "not found"; the owner's choice or
correction ("আমি Premiere Pro বলেছি") is learned as an alias for next time.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from core.apps.catalog import AppEntry, Catalog, scan
from core.apps.phonetic import has_bangla, is_acronym, key, normalize, similarity, spelled
from core.log import get_logger, redact

_log = get_logger("core")

SURE = 0.86          # sound-alike score that is acted on directly
ASK = 0.66           # below SURE but at least this → ask / AI fallback
MARGIN = 0.06        # two different apps closer than this → ask
PENDING_SECONDS = 300
CORRECTION_SECONDS = 900

OPEN_RX = re.compile(
    r"\b(open|opn|kholo|kolo|khol|khulo|khule\s*dao|launch|start|chalu\s*koro|chalao|run)\b|"
    r"(ওপেন|অপেন|উপেন|ওপিন|অপন|ওপন|পেন\s*কর|খোলো|খুলো|খোল|খুলে\s*দাও|কোলো|চালু|চালো|চালাও|"
    r"লঞ্চ|স্টার্ট)",
    re.IGNORECASE)
CLOSE_RX = re.compile(
    r"\b(close|bondho|bondo|band\s*koro|quit|exit|off\s*koro)\b|(বন্ধ|ক্লোজ|কোলোজ)",
    re.IGNORECASE)
# a file, folder, web page or path is not an app to open
GUARD_RX = re.compile(
    r"\b(file|files|folder|directory|website|site|web\s*page|link|url|project|document|"
    r"pdf|docx?|xlsx|mp4|mp3|jpg|png|zip)\b|[a-z]:\\|https?://|\.[a-z]{2,4}\b|"
    r"ফাইল|ফোল্ডার|ওয়েবসাইট|প্রজেক্ট|লিংক|ডকুমেন্ট", re.IGNORECASE)
APP_WORD_RX = re.compile(r"\b(app|apps|application|software|program)\b|"
                         r"অ্যাপ|এপ|এপস|অ্যাপস|সফটওয়্যার|সফটওয়ার|প্রোগ্রাম", re.IGNORECASE)

# words around an app name in a command, never part of it
STOP = frozenset((
    "open", "opn", "kholo", "khol", "khulo", "khule", "launch", "start", "chalu", "chalao",
    "run", "close", "bondho", "bondo", "band", "off", "quit", "exit", "koro", "kore", "dao",
    "den", "please", "plz", "pls", "nova", "nobha", "amar", "amer", "amr", "my", "the", "a",
    "an", "to", "pc", "computer", "te", "e", "ta", "ti", "er", "ke", "app", "apps",
    "application", "software", "program", "just", "tumi", "ektu", "now", "ekhon", "jodi",
    "and", "abar", "again", "for", "me", "in", "on", "ei", "oi", "ota", "eta",
    "ওপেন", "অপেন", "উপেন", "ওপিন", "অপন", "ওপন", "পেন", "খোলো", "খুলো", "খোল", "কোলো",
    "খুলে", "চালু", "চালো", "চালাও", "লঞ্চ", "স্টার্ট", "বন্ধ", "ক্লোজ", "করো", "করে", "কর",
    "করুন", "দাও", "দিন", "দেও", "প্লিজ", "নোভা", "নোবা", "আমার", "পিসিতে", "পিসি", "পিসির",
    "কম্পিউটারে", "টা", "টি", "টাকে", "অ্যাপ", "অ্যাপস", "এপ", "এপস", "এপ্স", "অ্যাপ্লিকেশন",
    "সফটওয়্যার", "সফটওয়ার", "প্রোগ্রাম", "জাস্ট", "তুমি", "একটু", "এখন", "আবার", "এই", "ওই",
    "ওটা", "এটা", "তো", "একটা",
))
_GLUED = ("খোলো", "খুলো", "কোলো", "ওপেন", "অপেন", "উপেন", "ওপিন", "ওপন", "পেন", "চালু",
          "করো")
_YES = re.compile(r"^(হ্যাঁ|হ্যা|হা|হুম|ঠিক\s*আছে|ঠিক|ha+|haa|hya|hum+|yes|yeah|ok|okay|"
                  r"thik\s*ache|ji|জি)[.!\s]*$", re.IGNORECASE)
_FIRST = re.compile(r"^(প্রথম\S*|first|1st|prothom\S*)$", re.IGNORECASE)
_SECOND = re.compile(r"^(দ্বিতীয়\S*|second|2nd|ditiyo\S*)$", re.IGNORECASE)
_THIRD = re.compile(r"^(তৃতীয়\S*|third|3rd|tritiyo\S*)$", re.IGNORECASE)
_NUM = {"1": 0, "2": 1, "3": 2, "১": 0, "২": 1, "৩": 2, "এক": 0, "দুই": 1, "তিন": 2,
        "ek": 0, "dui": 1, "tin": 2, "one": 0, "two": 1, "three": 2}
CORRECTION_RX = [
    re.compile(r"^(?:না[,।\s]*|নাহ[,।\s]*)?(?:আমি\s+)?(?:তো\s+)?(?P<x>.+?)\s+(?:বলেছি|বলছি|বলসি|"
               r"বলেছিলাম|বুঝিয়েছি|বুঝাইছি|চেয়েছি)[।.!\s]*$"),
    re.compile(r"^(?:na[,\s]*|nah[,\s]*)?(?:ami\s+)?(?:to\s+)?(?P<x>.+?)\s+(?:bolechi|bolsi|"
               r"bolchi|bolesi|bolechilam|bujhiyechi|bujhaisi|cheyechi)[.!\s]*$", re.IGNORECASE),
    re.compile(r"^(?:no[,\s]*)?i\s+(?:said|meant|wanted)\s+(?P<x>.+?)[.!\s]*$", re.IGNORECASE),
]


class AliasStore(Protocol):
    def all(self) -> dict[str, str]: ...
    def learn(self, phrase: str, app_id: str, source: str) -> bool: ...


class MemoryAliases:
    """Learned aliases kept in memory (tests, no database)."""

    def __init__(self) -> None:
        self.aliases: dict[str, str] = {}

    def all(self) -> dict[str, str]:
        return dict(self.aliases)

    def learn(self, phrase: str, app_id: str, source: str) -> bool:
        self.aliases[phrase] = app_id
        return True


@dataclass(frozen=True)
class Match:
    app: AppEntry
    score: float
    method: str          # exact | normalized | alias | learned | fuzzy | ai
    phrase: str


@dataclass(frozen=True)
class Resolution:
    action: str                          # open | close
    status: str                          # sure | missing | ask | none
    phrase: str
    best: Match | None
    candidates: tuple[Match, ...] = ()   # installed apps to offer, best first

    @property
    def app(self) -> AppEntry | None:
        return self.best.app if self.best is not None and self.status == "sure" else None


@dataclass
class _Pending:
    action: str
    phrase: str
    choices: list[str]
    expires: float


@dataclass
class _Attempt:
    action: str
    phrase: str
    app_id: str | None
    at: float


@dataclass(frozen=True)
class _Alias:
    app_id: str
    text: str
    norm: str
    key: str
    method: str


@dataclass
class Choice:
    """A pending question answered, or a correction understood."""
    action: str
    app: AppEntry
    phrase: str
    learned: bool = False


def safe_alias(phrase: str) -> bool:
    """Only short, plain names are learned — never anything secret-looking."""
    p = normalize(phrase)
    words = p.split()
    return (2 <= len(p) <= 40 and 1 <= len(words) <= 4 and redact(p) == p
            and redact(phrase) == phrase
            and not re.search(r"\d{3,}", p) and not all(w in STOP for w in words))


def _tokens(text: str) -> list[str]:
    out: list[str] = []
    for t in normalize(text).split():
        for g in _GLUED:                  # Whisper glues the verb on: "প্রখোলো"
            if t.endswith(g) and len(t) > len(g) + 1:
                out += [t[: -len(g)], g]
                break
        else:
            out.append(t)
    return out


@dataclass
class AppResolver:
    catalog_path: Path
    learned: AliasStore = field(default_factory=MemoryAliases)
    scanner: Callable[[], Catalog] = scan
    refresh_seconds: float = 600.0
    clock: Callable[[], float] = time.time
    _catalog: Catalog | None = None
    _index: list[_Alias] = field(default_factory=list)
    _index_of: tuple[float, int] = (-1.0, -1)
    _pending: dict[str, _Pending] = field(default_factory=dict)
    _last: dict[str, _Attempt] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # ---------------------------------------------------------------- catalog

    @property
    def catalog(self) -> Catalog:
        if self._catalog is None:
            self._catalog = Catalog.load(self.catalog_path) or self.rescan()
        return self._catalog

    def rescan(self) -> Catalog:
        cat = self.scanner()
        cat.save(self.catalog_path)
        self._catalog = cat
        return cat

    def refresh_if_stale(self) -> bool:
        """A name not found may be a just-installed app: rescan (at most every
        `refresh_seconds`)."""
        if self.clock() - self.catalog.scanned_at < self.refresh_seconds:
            return False
        self.rescan()
        return True

    def names(self, limit: int = 40) -> list[str]:
        """Short installed app names (Whisper vocabulary hint)."""
        out: list[str] = []
        for a in self.catalog.installed():
            short = min((n for n in a.aliases if n.isascii()), key=len, default=a.name)
            if a.kind != "uwp" and short not in out:
                out.append(short)
        return out[:limit]

    def _build_index(self) -> list[_Alias]:
        learned = self.learned.all()
        stamp = (self.catalog.scanned_at, hash(tuple(sorted(learned.items()))))
        if stamp == self._index_of:
            return self._index
        idx: list[_Alias] = []
        for a in self.catalog.apps.values():
            texts = dict.fromkeys([a.name, *a.aliases, a.id.replace("-", " ")])
            for t in texts:
                method = "exact" if t == a.name else "alias"
                idx.append(_Alias(a.id, t, normalize(t), key(t), method))
                words = normalize(t).split()
                if any(is_acronym(w) for w in words) and t.isascii():
                    sp = " ".join(spelled(w) if is_acronym(w) else w for w in words)
                    idx.append(_Alias(a.id, t, normalize(sp), key(sp), "alias"))
        for phrase, app_id in learned.items():
            if app_id in self.catalog.apps:
                idx.append(_Alias(app_id, phrase, normalize(phrase), key(phrase), "learned"))
        self._index, self._index_of = idx, stamp
        return idx

    # ---------------------------------------------------------------- matching

    def match(self, text: str, whole: bool = False) -> list[Match]:
        """Best match per app for any 1–4 word piece of `text` (or `whole` text only),
        best first."""
        toks = [t for t in _tokens(text) if t not in STOP]
        grams = [" ".join(toks)] if whole and toks else [
            " ".join(toks[i:i + n]) for n in range(1, 5) for i in range(len(toks) - n + 1)]
        best: dict[str, Match] = {}
        idx = self._build_index()
        for g in grams:
            gk = key(g)
            for al in idx:
                if g == al.norm:
                    m = Match(self.catalog.apps[al.app_id], 1.0,
                              "learned" if al.method == "learned" else (
                                  "exact" if al.method == "exact" else "alias"), g)
                elif len(gk) < 3 or len(al.key) < 3:
                    if not gk or gk != al.key or len(g) < 2:
                        continue
                    m = Match(self.catalog.apps[al.app_id], 0.97, "normalized", g)
                else:
                    s = similarity(gk, al.key)
                    if s < ASK - 0.1:
                        continue
                    m = Match(self.catalog.apps[al.app_id], 0.97 if s == 1.0 else s,
                              "normalized" if s == 1.0 else "fuzzy", g)
                old = best.get(m.app.id)
                if old is None or (m.score, len(m.phrase)) > (old.score, len(old.phrase)):
                    best[m.app.id] = m
        return sorted(best.values(), key=lambda m: (-m.score, -len(m.phrase), m.app.id))

    def _offers(self, phrase: str, skip: str = "") -> tuple[Match, ...]:
        """Installed apps closest to the phrase that named the app (not to some other
        word of the sentence)."""
        ms = [m for m in self.match(phrase, whole=True) if m.app.installed and m.app.id != skip
              and m.score >= ASK - 0.1]
        return tuple(ms[:3])

    def resolve(self, text: str, action: str) -> Resolution:
        ms = self.match(text)
        offer = self._offers(ms[0].phrase) if ms else ()
        if not ms or ms[0].score < ASK:
            # the words left once the command words are gone: what the owner may correct
            rest = " ".join([t for t in _tokens(text) if t not in STOP][:4])
            return Resolution(action, "none", rest, None, offer)
        best = ms[0]
        rival = next((m for m in ms[1:] if m.app.id != best.app.id), None)
        clear = rival is None or rival.score < 0.8 or best.score - rival.score >= MARGIN
        if best.score >= SURE and clear:
            if best.app.installed:
                return Resolution(action, "sure", best.phrase, best, (best,))
            return Resolution(action, "missing", best.phrase, best,
                              self._offers(best.phrase, skip=best.app.id))
        return Resolution(action, "ask", best.phrase, best, offer)

    def resolve_command(self, text: str, chat_id: str = "") -> Resolution | None:
        """None = not an app open/close request (let the other rules have it)."""
        if GUARD_RX.search(text):
            return None
        action = "close" if CLOSE_RX.search(text) else "open" if OPEN_RX.search(text) else ""
        if not action:
            return None
        with self._lock:
            res = self.resolve(text, action)
            named_app = res.status != "none" or bool(APP_WORD_RX.search(text))
            if named_app and res.status != "sure" and self.refresh_if_stale():
                res = self.resolve(text, action)       # maybe installed since the last scan
        self.note_attempt(chat_id, res)                # "আমি X বলেছি" may follow
        if res.status == "none" and not APP_WORD_RX.search(text):
            return None
        return res

    def is_safe_open(self, text: str) -> bool:
        """Opening a clearly named installed app cannot do harm, so a voice command for
        it needs no extra ✅ (closing may lose work — it keeps the strict check)."""
        if GUARD_RX.search(text) or CLOSE_RX.search(text) or not OPEN_RX.search(text):
            return False
        try:
            return self.resolve(text, "open").status == "sure"
        except Exception:  # noqa: BLE001 - unsure → the normal (strict) check
            return False

    # ------------------------------------------------- questions and corrections

    def note_attempt(self, chat_id: str, res: Resolution) -> None:
        now = self.clock()
        if res.phrase:
            app = res.app
            self._last[chat_id] = _Attempt(res.action, res.phrase, app.id if app else None, now)
        if res.status in ("ask", "missing") and res.candidates:
            self._pending[chat_id] = _Pending(res.action, res.phrase,
                                              [m.app.id for m in res.candidates],
                                              now + PENDING_SECONDS)
        else:
            self._pending.pop(chat_id, None)

    def note_picked(self, chat_id: str, action: str, phrase: str, app_id: str) -> None:
        """The AI fallback chose: no question is open any more, a correction may follow."""
        self._pending.pop(chat_id, None)
        self._last[chat_id] = _Attempt(action, phrase, app_id, self.clock())

    def reorder_pending(self, chat_id: str, order: list[str]) -> None:
        p = self._pending.get(chat_id)
        if p is not None:
            p.choices = list(order)

    def answer(self, chat_id: str, text: str) -> Choice | None:
        """The owner answering Nova's "which one?" — a number, yes, or a name."""
        p = self._pending.get(chat_id)
        if p is None or p.expires < self.clock():
            self._pending.pop(chat_id, None)
            return None
        t = normalize(text).strip(" .।!?")
        pick: int | None = _NUM.get(t)
        if pick is None and (_YES.match(t) or _FIRST.match(t)):
            pick = 0
        elif pick is None and _SECOND.match(t):
            pick = 1
        elif pick is None and _THIRD.match(t):
            pick = 2
        app_id = p.choices[pick] if pick is not None and pick < len(p.choices) else None
        if app_id is None and len(t.split()) <= 3:
            ms = [m for m in self.match(t) if m.app.id in p.choices and m.score >= SURE]
            app_id = ms[0].app.id if ms else None
        if app_id is None:
            return None
        del self._pending[chat_id]
        app = self.catalog.apps[app_id]
        learned = self._learn(p.phrase, app, "owner choice")
        return Choice(p.action, app, p.phrase, learned)

    def correction(self, chat_id: str, text: str) -> Choice | None:
        """"আমি Premiere Pro বলেছি" right after a wrong / unclear app request."""
        last = self._last.get(chat_id)
        if last is None or self.clock() - last.at > CORRECTION_SECONDS:
            return None
        for rx in CORRECTION_RX:
            m = rx.match(text.strip())
            if m:
                break
        else:
            return None
        res = self.resolve(m.group("x"), last.action)
        if res.app is None or res.app.id == last.app_id and last.app_id is not None:
            return None
        self._pending.pop(chat_id, None)
        learned = self._learn(last.phrase, res.app, "owner correction")
        self._last[chat_id] = _Attempt(last.action, last.phrase, res.app.id, self.clock())
        return Choice(last.action, res.app, last.phrase, learned)

    def _learn(self, phrase: str, app: AppEntry, source: str) -> bool:
        p = normalize(phrase)
        if not safe_alias(p) or any(normalize(a) == p for a in (app.name, *app.aliases)):
            return False
        ok = self.learned.learn(p, app.id, source)
        if ok:
            _log.info(f"app alias learned: «{p}» → {app.id} ({source})",
                      extra={"action": "apps.learn", "status": "ok"})
        return ok


def is_bangla_reply(text: str) -> bool:
    return has_bangla(text) or bool(re.search(r"\b(koro|kholo|bondho|dao|amar|ami)\b", text,
                                              re.IGNORECASE))
