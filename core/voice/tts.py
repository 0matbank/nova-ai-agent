"""Spoken replies (plan §57 "Optional TTS reply").

Text → speakable text (no emoji / markdown / links / "Verifier:" lines;
symbols read as words) → Microsoft neural voice via edge-tts (voice extra).
The MP3 is sent as-is (Telegram voice notes accept MP3): no second lossy
re-encode. Any failure returns None — the text reply is always sent anyway.
"""

from __future__ import annotations

import importlib
import re
from typing import Any

from core.config.schema import TTSSection
from core.log import get_logger

_log = get_logger("core")
_BN_CHARS = re.compile(r"[ঀ-৿]")
_LATIN = re.compile(r"[A-Za-z]")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍⬀-⯿]")
_URL = re.compile(r"https?://\S+")
_SKIP_LINE = re.compile(r"^\s*(verifier:|অবস্থা:|/\w+)", re.IGNORECASE)

# Symbols a voice would read badly, per reply language.
_WORDS = {
    "bn": [(r"(\d)\s*%", r"\1 শতাংশ"), (r"(\d)\s*°C", r"\1 ডিগ্রি"),
           (r"(\d[\d.]*)\s*/\s*(\d[\d.]*)\s*GB", r"\2-এর মধ্যে \1 গিগাবাইট"),
           (r"(\d)\s*GB", r"\1 গিগাবাইট"), (r"(\d)\s*MB", r"\1 মেগাবাইট")],
    "en": [(r"(\d)\s*%", r"\1 percent"), (r"(\d)\s*°C", r"\1 degrees"),
           (r"(\d[\d.]*)\s*/\s*(\d[\d.]*)\s*GB", r"\1 of \2 gigabytes"),
           (r"(\d)\s*GB", r"\1 gigabytes"), (r"(\d)\s*MB", r"\1 megabytes")],
}


def language_of(text: str) -> str:
    return "bn" if len(_BN_CHARS.findall(text)) >= len(_LATIN.findall(text)) else "en"


def speakable(text: str, max_chars: int = 1200) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip() and not _SKIP_LINE.match(ln)]
    lang = language_of(" ".join(lines))
    sep = "। " if lang == "bn" else ". "
    out = sep.join(ln.strip().rstrip("।.") for ln in lines)
    out = _URL.sub("", _EMOJI.sub("", out))
    for pattern, repl in _WORDS[lang]:
        out = re.sub(pattern, repl, out)
    out = re.sub(r"[*_`#<>«»|()\[\]]+", " ", out)
    out = re.sub(r"\s+", " ", out).strip(" ।.")
    if len(out) > max_chars:
        cut = max(out.rfind("।", 0, max_chars), out.rfind(".", 0, max_chars))
        out = out[: cut + 1 if cut > max_chars // 2 else max_chars]
    return out


class Speaker:
    def __init__(self, settings: TTSSection) -> None:
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return self.settings.enabled and bool(self.settings.voices)

    async def synthesize(self, text: str) -> bytes | None:
        """MP3 bytes of the spoken reply, or None."""
        if not self.enabled:
            return None
        clean = speakable(text, self.settings.max_chars)
        if not clean:
            return None
        lang = language_of(clean)
        voice = self.settings.voices.get(lang) or next(iter(self.settings.voices.values()))
        try:
            edge_tts: Any = importlib.import_module("edge_tts")
            mp3 = bytearray()
            async for chunk in edge_tts.Communicate(clean, voice,
                                                    rate=self.settings.rate).stream():
                if chunk["type"] == "audio":
                    mp3 += chunk["data"]
            return bytes(mp3) or None
        except Exception as e:
            _log.warning(f"tts failed ({type(e).__name__}: {e}) — text only",
                         extra={"action": "voice.tts", "status": "error"})
            return None
