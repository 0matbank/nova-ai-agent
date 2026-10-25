"""Spoken replies (plan §57 "Optional TTS reply").

Text → speakable text (no emoji / markdown / links / "Verifier:" lines;
symbols read as words) → Microsoft neural voice via edge-tts (voice extra).
The MP3 is sent as-is (Telegram voice notes accept MP3): no second lossy
re-encode. Any failure returns None — the text reply is always sent anyway.
"""

from __future__ import annotations

import base64
import contextlib
import importlib
import io
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import SecretStr

from core.config.schema import TTSSection
from core.log import get_logger

_log = get_logger("core")
_BN_CHARS = re.compile(r"[ঀ-৿]")
_LATIN = re.compile(r"[A-Za-z]")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍⬀-⯿]")
_URL = re.compile(r"https?://\S+")
_SKIP_LINE = re.compile(r"^\s*(verifier:|অবস্থা:|/\w+)", re.IGNORECASE)

# Symbols a voice would read badly, per reply language. For Bangla also the common
# English words of status results (units, free/used/of, uptime) — V1.2 owner 2026-10-25:
# «আমি বাংলায় বললাম, ইংরেজিতে কেন পড়ে শোনায়»
_WORDS = {
    "bn": [(r"(\d)\s*%", r"\1 শতাংশ"), (r"(\d)\s*°C", r"\1 ডিগ্রি"),
           (r"(\d[\d.]*)\s*GB\s+free\s+of\s+(\d[\d.]*)\s*GB",
            r"\2 গিগাবাইটের মধ্যে \1 গিগাবাইট খালি"),
           (r"(\d[\d.]*)\s*/\s*(\d[\d.]*)\s*GB", r"\2-এর মধ্যে \1 গিগাবাইট"),
           (r"(\d)\s*GB", r"\1 গিগাবাইট"), (r"(\d)\s*MB", r"\1 মেগাবাইট"),
           (r"(\d+)\s*h\s+(\d+)\s*m\b", r"\1 ঘণ্টা \2 মিনিট"),
           (r"(\d+)\s*d\s+(\d+)\s*h\b", r"\1 দিন \2 ঘণ্টা"),
           (r"\bdisk\s+free\b", "ডিস্কে খালি"), (r"\buptime\b", "চালু আছে"),
           (r"\bdrives\b", "ড্রাইভ"), (r"\bfree\b", "খালি"), (r"\bused\b", "ব্যবহৃত"),
           (r"\btotal\b", "মোট"), (r"\bof\b", "এর মধ্যে"), (r"\bvolume\b", "ভলিউম")],
    "en": [(r"(\d)\s*%", r"\1 percent"), (r"(\d)\s*°C", r"\1 degrees"),
           (r"(\d[\d.]*)\s*/\s*(\d[\d.]*)\s*GB", r"\1 of \2 gigabytes"),
           (r"(\d)\s*GB", r"\1 gigabytes"), (r"(\d)\s*MB", r"\1 megabytes")],
}


# A Bangla voice cannot read Latin acronyms (real check 2026-10-25: "CPU" came out as
# «কিপি»): spell them with Bangla letter names; a few are said as words.
_BN_LETTER = dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", [
    "এ", "বি", "সি", "ডি", "ই", "এফ", "জি", "এইচ", "আই", "জে", "কে", "এল", "এম", "এন",
    "ও", "পি", "কিউ", "আর", "এস", "টি", "ইউ", "ভি", "ডাবলিউ", "এক্স", "ওয়াই", "জেড"],
    strict=True))
_BN_ACRONYM_WORD = {"RAM": "র্যাম", "VRAM": "ভি র্যাম", "OK": "ওকে", "WIFI": "ওয়াইফাই"}
_ACRONYM = re.compile(r"(?<![A-Za-z])([A-Z]{2,5})(?![A-Za-z])")
_DECIMAL = re.compile(r"(\d+)\.(\d+)")


def _bn_spoken(text: str) -> str:
    text = _DECIMAL.sub(r"\1 দশমিক \2", text)
    return _ACRONYM.sub(lambda m: _BN_ACRONYM_WORD.get(m[1]) or
                        "".join(_BN_LETTER[c] for c in m[1]), text)


def language_of(text: str) -> str:
    return "bn" if len(_BN_CHARS.findall(text)) >= len(_LATIN.findall(text)) else "en"


def voice_language(text: str) -> str:
    """The voice to read it: a Bangla voice reads "CPU" or "RAM" fine, an English voice
    cannot read Bangla at all — so a fair share of Bangla is enough."""
    return "bn" if 3 * len(_BN_CHARS.findall(text)) >= len(_LATIN.findall(text)) else "en"


def speakable(text: str, max_chars: int = 1200, lang: str | None = None) -> str:
    """`lang`: the language it will be spoken in (the owner's) — else the text's own."""
    lines = [ln for ln in text.splitlines() if ln.strip() and not _SKIP_LINE.match(ln)]
    lang = lang or language_of(" ".join(lines))
    sep = "। " if lang == "bn" else ". "
    out = sep.join(ln.strip().rstrip("।.") for ln in lines)
    out = _URL.sub("", _EMOJI.sub("", out))
    for pattern, repl in _WORDS[lang]:
        out = re.sub(pattern, repl, out, flags=re.IGNORECASE)
    if lang == "bn":
        out = _bn_spoken(out)
    out = re.sub(r"[*_`#<>«»|()\[\]]+", " ", out)
    out = re.sub(r"\s+", " ", out).strip(" ।.")
    if len(out) > max_chars:
        cut = max(out.rfind("।", 0, max_chars), out.rfind(".", 0, max_chars))
        out = out[: cut + 1 if cut > max_chars // 2 else max_chars]
    return out


def to_ogg_opus(audio: bytes) -> bytes:
    """Any container PyAV can read (WAV from Gemini) → OGG/Opus voice note."""
    av: Any = importlib.import_module("av")        # PyAV ships with faster-whisper
    src = av.open(io.BytesIO(audio))
    out = io.BytesIO()
    dst = av.open(out, "w", format="ogg")
    stream = dst.add_stream("libopus", rate=48000)
    stream.layout = "mono"
    stream.bit_rate = 64000
    resampler = av.AudioResampler(format="s16", layout="mono", rate=48000)
    for frame in src.decode(audio=0):
        for f in resampler.resample(frame):
            for packet in stream.encode(f):
                dst.mux(packet)
    for packet in stream.encode(None):
        dst.mux(packet)
    dst.close()
    src.close()
    return out.getvalue()


class TTSBudget:
    """Budget guard (plan §9): characters sent to the API-billed voice per UTC day,
    persisted so restarts cannot reset it."""

    def __init__(self, path: Path | None, daily_chars: int) -> None:
        self.path = path
        self.daily_chars = daily_chars
        self._day, self._used = "", 0
        if path is not None and path.exists():
            with contextlib.suppress(ValueError, OSError):
                data = json.loads(path.read_text(encoding="utf-8"))
                self._day, self._used = str(data["day"]), int(data["used"])

    def _today(self) -> str:
        return datetime.now(UTC).strftime("%Y-%m-%d")

    def allows(self, chars: int) -> bool:
        if self._day != self._today():
            self._day, self._used = self._today(), 0
        return self._used + chars <= self.daily_chars

    def spend(self, chars: int) -> None:
        self.allows(0)
        self._used += chars
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"day": self._day, "used": self._used}),
                                 encoding="utf-8")


def rate_limit_info(r: httpx.Response) -> tuple[float | None, str]:
    """(seconds to wait, which quota) from a 429 — Retry-After or Google's RetryInfo."""
    wait: float | None = None
    quota = ""
    header = r.headers.get("retry-after", "")
    if header.replace(".", "", 1).isdigit():
        wait = float(header)
    try:
        details = r.json().get("error", {}).get("details", [])
    except ValueError:
        details = []
    for d in details if isinstance(details, list) else []:
        delay = str(d.get("retryDelay", ""))
        if delay.endswith("s") and delay[:-1].replace(".", "", 1).isdigit():
            wait = float(delay[:-1])
        for v in d.get("violations", []) or []:
            quota = quota or str(v.get("quotaId", ""))[:80]
    if wait is not None:
        wait = min(max(wait, 5.0), 6 * 3600.0)
    return wait, quota


class GeminiTTS:
    """Google Gemini speech generation (REST `interactions`, verified 2026-09-25)."""

    URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
    COOLDOWN_SECONDS = 600

    def __init__(self, api_key: SecretStr, model: str, voice: str, style: str,
                 client: httpx.AsyncClient | None = None) -> None:
        self.api_key = api_key
        self.model = model
        self.voice = voice
        self.style = style
        self._client = client or httpx.AsyncClient(timeout=60)
        self.cooldown_until = 0.0

    async def synthesize(self, text: str) -> bytes:
        if time.time() < self.cooldown_until:
            raise RuntimeError("gemini tts cooling down after a rate limit")
        item: dict[str, Any] = {"type": "text", "text": text}
        if self.style:
            item["annotations"] = [{"type": "speech_metadata", "style": self.style}]
        body = {"model": self.model,
                "input": [{"type": "user_input", "content": [item]}],
                "response_format": {"type": "audio"},
                "generation_config": {"speech_config": [{"voice": self.voice}]}}
        try:
            r = await self._client.post(self.URL, json=body, headers={
                "x-goog-api-key": self.api_key.get_secret_value()})
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise RuntimeError(f"gemini tts network error ({type(e).__name__})") from None
        if r.status_code == 429:
            # wait as long as Google says (a per-minute limit is seconds, a daily one hours)
            wait, quota = rate_limit_info(r)
            self.cooldown_until = time.time() + (wait or self.COOLDOWN_SECONDS)
            raise RuntimeError(f"gemini tts rate limited (free tier"
                               f"{', ' + quota if quota else ''}; "
                               f"retry in {int(wait or self.COOLDOWN_SECONDS)}s)")
        if r.status_code in (401, 403):
            raise RuntimeError(f"gemini tts auth error {r.status_code} — check GEMINI_API_KEY")
        if r.status_code != 200:
            raise RuntimeError(f"gemini tts HTTP {r.status_code}")
        audio = [c for step in r.json().get("steps", []) if step.get("type") == "model_output"
                 for c in step.get("content", []) if c.get("type") == "audio"]
        if not audio:
            raise RuntimeError("gemini tts returned no audio")
        return to_ogg_opus(base64.b64decode(audio[-1]["data"]))

    async def aclose(self) -> None:
        await self._client.aclose()


class Speaker:
    """Gemini first (natural), Microsoft edge voice as the fallback."""

    def __init__(self, settings: TTSSection, gemini: GeminiTTS | None = None,
                 budget: TTSBudget | None = None) -> None:
        self.settings = settings
        self.gemini = gemini if settings.engine == "gemini" else None
        self.budget = budget or TTSBudget(None, settings.daily_char_budget)
        self.last_engine = ""

    @property
    def enabled(self) -> bool:
        return self.settings.enabled and (self.gemini is not None or bool(self.settings.voices))

    async def synthesize(self, text: str) -> bytes | None:
        """Voice-note bytes (OGG/Opus from Gemini, MP3 from edge), or None."""
        if not self.enabled:
            return None
        clean = speakable(text, self.settings.max_chars, voice_language(text))
        if not clean:
            return None
        if self.gemini is not None and self.budget.allows(len(clean)):
            try:
                audio = await self.gemini.synthesize(clean)
                self.budget.spend(len(clean))
                self.last_engine = "gemini"
                return audio
            except Exception as e:
                _log.warning(f"gemini tts unavailable ({e}) — using edge voice",
                             extra={"action": "voice.tts", "status": "fallback"})
        return await self._edge(clean)

    async def _edge(self, clean: str) -> bytes | None:
        if not self.settings.voices:
            return None
        lang = voice_language(clean)
        voice = self.settings.voices.get(lang) or next(iter(self.settings.voices.values()))
        try:
            edge_tts: Any = importlib.import_module("edge_tts")
            mp3 = bytearray()
            async for chunk in edge_tts.Communicate(clean, voice,
                                                    rate=self.settings.rate).stream():
                if chunk["type"] == "audio":
                    mp3 += chunk["data"]
            self.last_engine = "edge"
            return bytes(mp3) or None
        except Exception as e:
            _log.warning(f"tts failed ({type(e).__name__}: {e}) — text only",
                         extra={"action": "voice.tts", "status": "error"})
            return None
