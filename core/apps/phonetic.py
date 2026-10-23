"""Sound-alike keys for app names (V1.1): Whisper writes English app names in Bangla
script ("বিএস কোট" for "VS Code", "কেপকাত" for "CapCut") — both sides are turned
into the same rough Latin sound key, so they can be compared.

    romanize("ক্যাপকাট") -> "kapkat"        key("CapCut") == key("ক্যাপকাট") == "kapkat"
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

_CONS = {
    "ক": "k", "খ": "kh", "গ": "g", "ঘ": "gh", "ঙ": "ng", "চ": "ch", "ছ": "ch", "জ": "j",
    "ঝ": "jh", "ঞ": "n", "ট": "t", "ঠ": "th", "ড": "d", "ঢ": "dh", "ণ": "n", "ত": "t",
    "থ": "th", "দ": "d", "ধ": "dh", "ন": "n", "প": "p", "ফ": "f", "ব": "b", "ভ": "bh",
    "ম": "m", "য": "j", "র": "r", "ল": "l", "শ": "sh", "ষ": "sh", "স": "s", "হ": "h",
    "ড়": "r", "ঢ়": "rh", "য়": "y", "ৎ": "t",
    # Whisper writes this unassigned code point for a "v"/"ভ" sound ("঵ল্চ" = VLC)
    "঵": "bh",
}
_VOWELS = {"অ": "o", "আ": "a", "ই": "i", "ঈ": "i", "উ": "u", "ঊ": "u", "ঋ": "ri", "এ": "e",
           "ঐ": "oi", "ও": "o", "ঔ": "ou"}
_SIGNS = {"া": "a", "ি": "i", "ী": "i", "ু": "u", "ূ": "u", "ৃ": "ri", "ে": "e", "ৈ": "oi",
          "ো": "o", "ৌ": "ou"}
_MARKS = {"ং": "ng", "ঃ": "h", "ঁ": "", "়": "", "‌": "", "‍": ""}
_VIRAMA = "্"
_NUKTA_FIX = {"ড়": "ড়", "ঢ়": "ঢ়", "য়": "য়"}
_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


def has_bangla(text: str) -> bool:
    return any("ঀ" <= c <= "৿" for c in text)


def romanize(text: str) -> str:
    """Bangla script → rough Latin (other characters pass through, lower-cased)."""
    s = unicodedata.normalize("NFC", text)
    for a, b in _NUKTA_FIX.items():
        s = s.replace(a, b)
    s = s.translate(_DIGITS)
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c in _CONS:
            nxt = s[i + 1] if i + 1 < n else ""
            if c == "য" and out and out[-1][-1:] and out[-1][-1] in "aeiou":
                out.append("y")              # য after a vowel sounds like য়
            else:
                out.append(_CONS[c])
            if nxt in _SIGNS:
                out.append(_SIGNS[nxt])
                i += 2
                continue
            if nxt == _VIRAMA:
                after = s[i + 2] if i + 2 < n else ""
                if after == "য":             # ya-phala (ক্যা) only colours the vowel
                    i += 3
                    continue
                i += 2
                continue
            i += 1                           # the inherent vowel is mostly silent in names
            continue
        if c in _VOWELS:
            out.append(_VOWELS[c])
        elif c in _SIGNS:
            out.append(_SIGNS[c])
        elif c in _MARKS:
            out.append(_MARKS[c])
        elif c == _VIRAMA:
            pass
        else:
            out.append(c.lower())
        i += 1
    return "".join(out)


_LETTER_NAMES = {
    "a": "e", "b": "bi", "c": "si", "d": "di", "e": "i", "f": "ef", "g": "ji", "h": "eich",
    "i": "ai", "j": "je", "k": "ke", "l": "el", "m": "em", "n": "en", "o": "o", "p": "pi",
    "q": "kiu", "r": "ar", "s": "es", "t": "ti", "u": "iu", "v": "bhi", "w": "dablu",
    "x": "eks", "y": "oai", "z": "jed",
}


def spelled(token: str) -> str:
    """'obs' → 'o bi es': how an acronym sounds when spoken letter by letter."""
    return " ".join(_LETTER_NAMES.get(c, c) for c in token.lower())


def is_acronym(token: str) -> bool:
    t = token.lower()
    return 2 <= len(t) <= 4 and t.isalpha() and sum(c in "aeiou" for c in t) <= 1 \
        and t not in ("pro", "pc", "app", "the", "and", "for")


_RULES = [
    (r"ph", "f"), (r"chr", "kr"), (r"ch", "j"), (r"sh", "s"), (r"ck", "k"), (r"qu", "k"),
    (r"c(?=[eiy])", "s"), (r"c", "k"), (r"q", "k"), (r"x", "ks"), (r"z", "j"),
    (r"[vw]", "b"), (r"[dt]", "t"), (r"g", "k"), (r"[hy]", ""), (r"[aeiou]+", "a"),
]
_RULES_RX = [(re.compile(a), b) for a, b in _RULES]


def key(text: str) -> str:
    """A sound key: script-free, voiced/unvoiced pairs merged, vowels collapsed."""
    # English words drop a silent final e: "code", "chrome", "capture"
    s = " ".join(_SILENT_E.sub("", w) if w.isascii() and len(w) > 3 else w
                 for w in romanize(text).lower().split())
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    for rx, rep in _RULES_RX:
        s = rx.sub(rep, s)
    s = re.sub(r"\s+", "", s)
    return re.sub(r"(.)\1+", r"\1", s)        # double letters


_SILENT_E = re.compile(r"(?<=[b-df-hj-np-tv-z])e$")


def normalize(text: str) -> str:
    """Same words, same form: lower case, punctuation and extra spaces gone."""
    s = unicodedata.normalize("NFC", text).lower()
    # keep letters, digits and Bangla vowel signs (combining marks are not \w)
    s = "".join(c if c.isalnum() or c in "+#." or unicodedata.category(c)[0] == "M"
                else " " for c in s)
    s = re.sub(r"(?<!\w)\.|\.(?!\w)", " ", s)
    return " ".join(s.split())


def similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()
