"""V1.2 Speech Dictionary: the owner's own words (projects, folders, people) and the
fixes for what Whisper keeps mishearing — applied to a voice transcript BEFORE routing,
and given to Whisper as hotwords.

A fix comes from the owner in two ways, never from a guess:
- taught directly ("শব্দ শেখো: পিছি মানে পিসি", "/words পিছি = পিসি") — active at once;
- confirmed: the owner tapped ✅ on a suggested reading. The same word-level change
  must be confirmed twice before it is applied by itself (one tap can be a one-off).

Only short plain phrases are kept — nothing secret-looking (`redact`).
Kept in `memories` (layer long_term): kind speech_fix (heard → meant), speech_word
(a word to listen for), speech_seen (a confirmed change waiting for its second tap).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from core.apps.phonetic import key, similarity, spelled
from core.db.models import Memory, utcnow
from core.log import get_logger, redact
from core.voice.cleanup import negations_in, numbers_in

_log = get_logger("core")

LAYER = "long_term"
FIX, WORD, SEEN = "speech_fix", "speech_word", "speech_seen"
CONFIRMS_TO_LEARN = 2
# letters on either side make it part of another word ("কি" is not the start of "কিছু")
_LETTER = r"0-9A-Za-zঀ-৿"
# the nukta letters have two spellings: base + nukta (what NFC gives), or one code
# point — a transcript may hold either
_NUKTA = {"\u09af\u09bc": "\u09df", "\u09a1\u09bc": "\u09dc", "\u09a2\u09bc": "\u09dd"}
# a Bangla word keeps its ending: "পিছির" is "পিছি" + "র"
SUFFIXES = tuple(dict.fromkeys(unicodedata.normalize("NFC", x) for x in (
    "\u09df\u09c7\u09b0", "দের", "গুলো", "গুলি", "টার", "টির", "টা", "টি", "তে", "কে", "ের",
    "এর", "র", "ে", "\u09df")))


def _spell(text: str) -> str:
    """A regex for `text` (in NFC) that also matches the one-code-point nukta letters."""
    body = re.escape(text)
    for pair, single in _NUKTA.items():
        body = body.replace(pair, f"(?:{pair}|{single})")
    return body


_SUFFIX = "(?:" + "|".join(_spell(x) for x in SUFFIXES) + ")?"
_PUNCT = " \t\"'«»“”‘’.,!?।:;()[]"


class Store(Protocol):
    def rows(self, kind: str) -> dict[str, tuple[str, str]]: ...   # key -> (content, source)
    def put(self, kind: str, key: str, content: str, source: str) -> None: ...
    def drop(self, kind: str, key: str) -> bool: ...


class DbStore:
    """The agent DB (`memories`, layer long_term), kept across restarts."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def rows(self, kind: str) -> dict[str, tuple[str, str]]:
        with self.sessions() as s:
            found = s.scalars(select(Memory).where(Memory.layer == LAYER,
                                                   Memory.kind == kind)).all()
            return {r.key: (r.content, r.source or "") for r in found}

    def put(self, kind: str, key: str, content: str, source: str) -> None:
        with self.sessions() as s:
            row = s.scalar(select(Memory).where(Memory.layer == LAYER, Memory.kind == kind,
                                                Memory.key == key[:200]))
            if row is None:
                s.add(Memory(layer=LAYER, kind=kind, key=key[:200], content=content,
                             source=source))
            else:
                row.content, row.source, row.updated_at = content, source, utcnow()
            s.commit()

    def drop(self, kind: str, key: str) -> bool:
        with self.sessions() as s:
            row = s.scalar(select(Memory).where(Memory.layer == LAYER, Memory.kind == kind,
                                                Memory.key == key[:200]))
            if row is None:
                return False
            s.delete(row)
            s.commit()
            return True


class MemoryStore:
    """In memory (tests, no database)."""

    def __init__(self) -> None:
        self.data: dict[str, dict[str, tuple[str, str]]] = {}

    def rows(self, kind: str) -> dict[str, tuple[str, str]]:
        return dict(self.data.get(kind, {}))

    def put(self, kind: str, key: str, content: str, source: str) -> None:
        self.data.setdefault(kind, {})[key] = (content, source)

    def drop(self, kind: str, key: str) -> bool:
        return self.data.get(kind, {}).pop(key, None) is not None


def clean(phrase: str) -> str:
    return " ".join(phrase.strip(_PUNCT).split())


def fold(phrase: str) -> str:
    """The form a phrase is kept under: one spelling, lower case."""
    return unicodedata.normalize("NFC", clean(phrase)).lower()


def safe_phrase(phrase: str, max_words: int = 4, max_len: int = 40) -> bool:
    p = clean(phrase)
    return (2 <= len(p) <= max_len and 1 <= len(p.split()) <= max_words
            and redact(p) == p and not re.search(r"\d{5,}", p))


def _pattern(phrase: str) -> str:
    body = _spell(fold(phrase))
    body = body.replace(r"\ ", r"\s+")
    return rf"(?<![{_LETTER}])({body}){_SUFFIX}(?![{_LETTER}])"


@dataclass(frozen=True)
class Applied:
    text: str
    changes: tuple[tuple[str, str], ...]      # (heard, meant) as they were in the text


class SpeechDictionary:
    def __init__(self, store: Store | None = None) -> None:
        self.store: Store = store if store is not None else MemoryStore()
        self.on_change: list[Callable[[], None]] = []
        self._rx: re.Pattern[str] | None = None
        self._fixes: dict[str, str] = {}

    # ------------------------------------------------------------- reading

    def fixes(self) -> dict[str, str]:
        return {k: v for k, (v, _s) in self.store.rows(FIX).items()}

    def words(self) -> list[str]:
        """Words to listen for: taught words, then what fixes turn into."""
        out = [v for v, _s in self.store.rows(WORD).values()]
        out += [v for v in self.fixes().values()]
        return list(dict.fromkeys(out))

    def apply(self, text: str) -> Applied:
        """Replace every learned mishearing in one pass (a fix never feeds another)."""
        if self._rx is None:
            self._fixes = self.fixes()
            keys = sorted(self._fixes, key=len, reverse=True)
            self._rx = (re.compile("|".join(_pattern(k) for k in keys), re.IGNORECASE)
                        if keys else re.compile(r"(?!x)x"))
        changes: list[tuple[str, str]] = []

        def swap(m: re.Match[str]) -> str:
            heard = next(g for g in m.groups() if g is not None)
            meant = self._fixes.get(fold(heard))
            if meant is None:
                return m.group(0)
            changes.append((heard, meant))
            return meant + m.group(0)[len(heard):]

        out = self._rx.sub(swap, text)
        return Applied(out, tuple(changes))

    # ------------------------------------------------------------- changing

    def _changed(self) -> None:
        self._rx = None
        for hook in self.on_change:
            try:
                hook()
            except Exception:  # noqa: BLE001 - a hint refresh must not lose the change
                _log.exception("speech dictionary hook failed",
                               extra={"action": "voice.dictionary"})

    def teach(self, heard: str, meant: str, source: str = "owner") -> bool:
        h, m = clean(heard), clean(meant)
        if not safe_phrase(h) or not safe_phrase(m, 6, 60) or fold(h) == fold(m):
            return False
        self.store.put(FIX, fold(h), m, source)
        self.store.drop(SEEN, fold(h))
        _log.info(f"speech fix learned: «{h}» → «{m}» ({source})",
                  extra={"action": "voice.dictionary", "status": "ok"})
        self._changed()
        return True

    def add_word(self, word: str) -> bool:
        w = clean(word)
        if not safe_phrase(w, 6, 60):
            return False
        self.store.put(WORD, fold(w), w, "owner")
        self._changed()
        return True

    def forget(self, phrase: str) -> bool:
        k = fold(phrase)
        gone = self.store.drop(FIX, k) | self.store.drop(WORD, k) | self.store.drop(SEEN, k)
        if not gone:
            # forgetting by what it turns into: "পিসি" removes "পিছি → পিসি" too
            for key, (meant, _s) in self.store.rows(FIX).items():
                if fold(meant) == k:
                    gone |= self.store.drop(FIX, key)
        if gone:
            self._changed()
        return gone

    def observe(self, heard_text: str, chosen_text: str,
                alike: Callable[[str, str], bool] | None = None,
                source: str = "confirmed", many: bool = False,
                noticed: list[tuple[str, str]] | None = None) -> list[tuple[str, str]]:
        """The owner confirmed `chosen_text` for what was heard as `heard_text`.
        Each small word-level change counts once; the second time it is learned.
        `alike`: only changes it accepts count (an edit must sound like a mishearing).
        `many`: several fixed words in one sentence (see `word_changes`).
        `noticed`: gets the changes counted for the first time."""
        learned: list[tuple[str, str]] = []
        # the same fix three times in one sentence is still one time (seen twice means
        # on two occasions)
        changes = {(fold(h), fold(m)): (h, m)
                   for h, m in word_changes(heard_text, chosen_text, many=many)}
        for h, m in changes.values():
            if alike is not None and not alike(h, m):
                continue
            k = fold(h)
            if k in self.store.rows(FIX):
                continue
            seen = self.store.rows(SEEN).get(k)
            count = int(seen[1]) + 1 if seen and fold(seen[0]) == fold(m) else 1
            if count >= CONFIRMS_TO_LEARN:
                if self.teach(h, m, source):
                    learned.append((h, m))
            else:
                self.store.put(SEEN, k, m, str(count))
                if noticed is not None:
                    noticed.append((h, m))
        return learned

    def listing(self) -> str:
        fixes = self.store.rows(FIX)
        words = [v for v, _s in self.store.rows(WORD).values()]
        if not fixes and not words:
            return ("📖 অভিধান খালি।\nশেখাতে লিখুন: «শব্দ শেখো: পিছি মানে পিসি» বা "
                    "«শব্দ শেখো: আপনার project-এর নাম»।")
        lines = ["📖 অভিধান"]
        for k, (meant, source) in sorted(fixes.items()):
            mark = {"confirmed": " (✅ থেকে শেখা)",
                    EDITED: " (আপনার সংশোধন থেকে শেখা)"}.get(source, "")
            lines.append(f"• «{k}» → «{meant}»{mark}")
        if words:
            lines.append("শুনতে চেষ্টা করি: " + ", ".join(sorted(words)))
        lines.append("মুছতে: «অভিধান থেকে <শব্দ> মুছো»")
        return "\n".join(lines)


def _tokens(text: str) -> list[str]:
    return [t for t in (clean(w) for w in text.split()) if t]


def stem_pair(heard: str, meant: str) -> tuple[str, str]:
    """Learn the word, not its ending: "পিছি" → "পিসির" is kept as "পিছি" → "পিসি", so a
    later "পিছির" becomes "পিসির" (the ending is carried over), never "পিসিরর"."""
    for sfx in SUFFIXES:
        if meant.endswith(sfx) and len(meant) > len(sfx) + 1:
            if heard.endswith(sfx) and len(heard) > len(sfx) + 1:
                return heard[:-len(sfx)], meant[:-len(sfx)]
            return heard, meant[:-len(sfx)]
    return heard, meant


EDITED = "edited"               # learned from the owner's own fix after dictation
ALIKE = 0.6
_LAST_VOWEL = re.compile("[া-ৌৗ]+$")


def _sound_keys(phrase: str) -> set[str]:
    keys = {key(phrase)}
    letters = phrase.replace(" ", "")
    if letters.isascii() and letters.isalpha() and letters.isupper() and len(letters) <= 6:
        keys.add(key(spelled(letters)))          # "PC" is said «পিসি»
    # spoken Bangladeshi Bangla blurs জ/য/চ/ছ with স/শ, and Whisper writes either
    # (owner 2026-10-26: «কাস» for «কাজ», «আসে» for «আছে», «পিছি» for «পিসি»)
    return {k.replace("j", "s") for k in keys}


def sounds_alike(heard: str, meant: str) -> bool:
    """Does the owner's edit look like a mishearing fixed, not a new idea? Measured
    2026-10-26 on Bangla pairs: «পিছি→পিসি», «সনাও→শোনাও», «পিসি→PC», «বড্ধমন→বর্তমান»
    sound alike (0.73-1.0); «কাল→কাজ», «দুই→তিন», «দেখা→লেখা», «ঢাকা→রাজশাহী» do not.
    A changed number or «না», or only a changed last vowel («কাকা→কাকি»: another
    word), is never a mishearing."""
    if numbers_in(heard) != numbers_in(meant) or negations_in(heard) != negations_in(meant):
        return False
    if _LAST_VOWEL.sub("", fold(heard)) == _LAST_VOWEL.sub("", fold(meant)):
        return False
    for a in _sound_keys(heard):
        for b in _sound_keys(meant):
            if not a or not b or a[0] != b[0]:
                continue
            # the same consonants: only a vowel heard wrong («সনাও» sna ↔ «শোনাও» sana);
            # else a short word must not change at all («কাল» kal ↔ «কাজ» kaj)
            if a.replace("a", "") == b.replace("a", "") or (
                    min(len(a), len(b)) >= 4 and similarity(a, b) >= ALIKE):
                return True
    return False


def word_changes(heard: str, chosen: str, many: bool = False) -> list[tuple[str, str]]:
    """Small replaced spans (≤3 words each side; word by word when the counts match).
    A wholly rewritten sentence teaches nothing — that is a different request, not a
    mishearing. `many` (the owner's own edit of dictated words): any number of fixed
    words, an added or removed word is passed over — while most words stay as they
    were (owner 2026-10-26 fixed six words of one sentence at once)."""
    a, b = _tokens(heard), _tokens(chosen)
    matcher = SequenceMatcher(None, [fold(t) for t in a], [fold(t) for t in b],
                              autojunk=False)
    ops = [op for op in matcher.get_opcodes() if op[0] != "equal"]
    if many:
        kept = sum(i2 - i1 for tag, i1, i2, _j1, _j2 in matcher.get_opcodes()
                   if tag == "equal")
        if 2 * kept < len(a):
            return []
        ops = [op for op in ops if op[0] == "replace"]
    elif len(ops) > 2 or any(op[0] != "replace" for op in ops):
        return []
    if not ops:
        return []
    out = []
    for _tag, i1, i2, j1, j2 in ops:
        if i2 - i1 > 3 or j2 - j1 > 3:
            if many:
                continue
            return []
        spans = (list(zip(a[i1:i2], b[j1:j2], strict=True)) if i2 - i1 == j2 - j1
                 else [(" ".join(a[i1:i2]), " ".join(b[j1:j2]))])
        for h, m in spans:
            h, m = stem_pair(h, m) if " " not in h + m else (h, m)
            if safe_phrase(h) and safe_phrase(m, 6, 60) and fold(h) != fold(m):
                out.append((h, m))
    return out


# ------------------------------------------------------------- the owner's commands

_TEACH = re.compile(
    r"^\s*(?:শব্দ\s*শেখো|শব্দ\s*শিখে\s*রাখো|অভিধানে\s*(?:যোগ\s*করো|রাখো|লেখো)|"
    r"(?:shobdo|sobdo|shabdo)\s*(?:shekho|sekho|shikhe\s*rakho)|"
    r"(?:abhidhan|ovidhan|dictionary)\s*e\s*(?:jog|add)\s*koro|learn\s*(?:the\s*)?word|"
    r"/words)\s*[:,\-–]?\s*(?P<rest>.+?)\s*$", re.IGNORECASE)
_MEANS = re.compile(r"\s+(?:মানে|বললে|mane|bolle|means)\s+|\s*(?:=|->|→)\s*",
                    re.IGNORECASE)
_LIST = re.compile(r"^\s*(?:/words|অভিধান\s*দেখাও|শব্দের\s*তালিকা|"
                   r"(?:abhidhan|ovidhan|dictionary)\s*(?:dekhao|dekhaw|show)|"
                   r"show\s*(?:my\s*)?(?:speech\s*)?(?:dictionary|words))\s*[.!?।]?\s*$",
                   re.IGNORECASE)
_FORGET = re.compile(
    r"^\s*(?:(?:অভিধান|abhidhan|ovidhan|dictionary)\s*(?:থেকে|theke|from)\s*(?P<x>.+?)\s*"
    r"(?:মুছো|মুছে\s*দাও|বাদ\s*দাও|muche\s*dao|mucho|bad\s*dao|remove|delete)|"
    r"/words\s+(?:forget|remove|delete)\s+(?P<y>.+?)|"
    r"(?:forget|remove)\s+(?:the\s*)?word\s+(?P<z>.+?))\s*[.!?।]?\s*$", re.IGNORECASE)


def dictionary_command(text: str) -> tuple[str, dict[str, str]] | None:
    """("list" | "forget" | "teach" | "word", params) — fixed rules, no AI."""
    if _LIST.match(text):
        return ("list", {})
    m = _FORGET.match(text)
    if m:
        return ("forget", {"phrase": clean(m.group("x") or m.group("y") or m.group("z"))})
    m = _TEACH.match(text)
    if m is None:
        return None
    # a leading space lets "শব্দ শেখো: মানে X" (nothing heard) split too — and be refused
    parts = _MEANS.split(" " + m.group("rest"), maxsplit=1)
    if len(parts) == 2:
        return ("teach", {"heard": clean(parts[0]), "meant": clean(parts[1])})
    return ("word", {"word": clean(m.group("rest"))})


def run_command(dictionary: SpeechDictionary, text: str) -> str | None:
    cmd = dictionary_command(text)
    if cmd is None:
        return None
    op, p = cmd
    if op == "list":
        return dictionary.listing()
    if op == "forget":
        return (f"🗑️ «{p['phrase']}» অভিধান থেকে মুছে দিলাম।" if dictionary.forget(p["phrase"])
                else f"«{p['phrase']}» অভিধানে নেই।")
    if op == "teach":
        if dictionary.teach(p["heard"], p["meant"]):
            return (f"📖 শিখলাম: কণ্ঠে «{p['heard']}» শুনলে «{p['meant']}» ধরব।")
        return "❌ শেখাতে পারলাম না — ছোট সাধারণ শব্দ দিন (গোপন তথ্য নয়, ৪ শব্দের মধ্যে)।"
    if dictionary.add_word(p["word"]):
        return f"📖 শিখলাম: «{p['word']}» শব্দটা কণ্ঠে শোনার চেষ্টা করব।"
    return "❌ শেখাতে পারলাম না — ছোট সাধারণ শব্দ দিন (গোপন তথ্য নয়)।"
