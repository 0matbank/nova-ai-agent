"""V1.2 dictation (plan §66-6): «লিখে দাও: …», «টাইপ করো: …», "type: …", "dictate: …" —
the words after the colon are typed at the cursor of the focused field
(desktop-ui.dictate). The colon makes it plain: what follows is the text itself, not a
request («একটা কবিতা লিখে দাও» is a request, «লিখে দাও: একটা কবিতা» is the words).
Spoken punctuation becomes the mark."""

from __future__ import annotations

import re
from typing import Any

PREFIX = "লিখে দাও: "                 # what the push-to-talk dictation key sends
_BN = "ঀ-৿"
_DICTATE = re.compile(
    r"^\s*(?:লিখে\s*দাও|টাইপ\s*করো|টাইপ\s*কর|likhe\s*dao|type\s*koro|type|dictate)"
    r"\s*[:：]\s*(?P<text>\S.*)$", re.IGNORECASE | re.DOTALL)


def _word(bn: str, en: str) -> str:
    return rf"(?:(?<![{_BN}]){bn}(?![{_BN}])|\b(?:{en})\b)"


# (spoken words, mark, space after it)
_MARKS = [
    (_word("নতুন\\s*লাইন|নিউ\\s*লাইন", r"new\s*line|notun\s*line"), "\n", ""),
    (_word("দাঁড়ি|দাড়ি|দাঁড়ি", r"dari|daari"), "।", " "),
    (_word("ফুলস্টপ|ফুল\\s*স্টপ", r"full\s*stop|period"), ".", " "),
    (_word("কমা", r"comma|koma"), ",", " "),
    (_word("প্রশ্নবোধক\\s*চিহ্ন|প্রশ্নচিহ্ন", r"question\s*mark"), "?", " "),
    (_word("বিস্ময়সূচক\\s*চিহ্ন|আশ্চর্যবোধক\\s*চিহ্ন", r"exclamation\s*mark"), "!", " "),
]


def with_marks(text: str) -> str:
    """«আমি আসছি দাঁড়ি নতুন লাইন ধন্যবাদ» → «আমি আসছি।\\nধন্যবাদ»"""
    for spoken, mark, after in _MARKS:
        text = re.sub(rf"[ \t]*{spoken}[ \t]*", mark + after if mark != "\n" else mark,
                      text, flags=re.IGNORECASE)
    return re.sub(r"[ \t]+\n", "\n", re.sub(r"[ \t]{2,}", " ", text)).strip(" \t")


def dictation_command(text: str) -> tuple[str, str, dict[str, Any]] | None:
    m = _DICTATE.match(text)
    if m is None:
        return None
    words = with_marks(m.group("text"))
    return ("desktop-ui", "dictate", {"text": words}) if words else None
