"""Speech-to-text with faster-whisper (plan §7).

- Loaded on first use, unloaded after `idle_unload_seconds` (plan §17H:
  Whisper never sits in VRAM permanently).
- GPU (float16) first; if CUDA fails the model loads on CPU/int8.
- VAD (Silero, built into faster-whisper) strips silence/noise.
- Language: auto-detect; if the result is neither Bangla nor English (Banglish is
  often mis-detected as Hindi/Urdu), re-run as Bangla and keep the more
  confident transcript.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import math
import os
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.log import get_logger

_log = get_logger("core")
OWNER_LANGUAGES = ("bn", "en")
MIN_WARM_SECONDS = 60.0     # even at critical memory pressure, stay this long after use
HOLD_SECONDS = 120.0        # a warm-up keeps the model until its transcription (at most)
INDIC = frozenset({"hi", "ur", "as", "ne", "mr", "pa", "or", "gu", "sa", "si"})


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str
    language_probability: float
    confidence: float           # 0..1, from mean segment log-probability
    duration: float
    seconds: float
    device: str


ModelFactory = Callable[[str, str, str], Any]    # (path, device, compute_type) -> model


_dll_dirs_added = False


def _add_cuda_dll_dirs() -> None:
    """On Windows the NVIDIA wheels (nvidia-cublas-cu12 / nvidia-cudnn-cu12, voice
    extra) ship their DLLs in site-packages/nvidia/*/bin, which is not on the DLL
    search path by default."""
    global _dll_dirs_added
    if _dll_dirs_added or sys.platform != "win32":
        return
    _dll_dirs_added = True
    try:
        # Optional (voice extra, Windows only): imported dynamically so type checking
        # does not depend on which extras are installed.
        nvidia = importlib.import_module("nvidia")
    except ImportError:
        return
    for base in list(nvidia.__path__):
        for sub in ("cublas", "cudnn", "cuda_nvrtc"):
            bin_dir = Path(base) / sub / "bin"
            if bin_dir.is_dir():
                os.add_dll_directory(str(bin_dir))
                os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"


def _default_factory(path: str, device: str, compute_type: str) -> Any:
    if device == "cuda":
        _add_cuda_dll_dirs()
    whisper_model = importlib.import_module("faster_whisper").WhisperModel  # voice extra
    return whisper_model(path, device=device, compute_type=compute_type)


class Transcriber:
    def __init__(self, model_path: Path, device: str = "cuda", compute_type: str = "float16",
                 idle_unload_seconds: float = 300, beam_size: int = 5,
                 factory: ModelFactory = _default_factory, hint_words: list[str] | None = None,
                 vad_speech_pad_ms: int = 400,
                 pressure: Callable[[], str] | None = None) -> None:
        self.model_path = model_path
        self.device = device
        self.compute_type = compute_type
        self.idle_unload_seconds = idle_unload_seconds
        self.beam_size = beam_size
        # Vocabulary bias (faster-whisper initial_prompt) for the owner's usual words.
        self.initial_prompt = ", ".join(hint_words) if hint_words else None
        self.vad_speech_pad_ms = vad_speech_pad_ms
        # V1.1: installed app names, so "Premiere Pro" is not heard as "প্রিমিয় প্রো"
        self.hotwords: str | None = None
        # V1.2: the owner's own words (Speech Dictionary) — first, so a long app list is
        # what gets cut at Whisper's prompt limit. Measured 2026-10-25 (real large-v3, two
        # edge Bangla voices): owner words as hotwords 6/6 heard right, no other phrase
        # worse; the same words in initial_prompt 5/6 and "পিসির অবস্থা" turned English.
        self.owner_words: str | None = None
        self.pressure = pressure
        self.last_load_seconds = 0.0      # cold load paid by the last transcription
        self._load_seconds = 0.0
        self._held_until = 0.0            # no idle unload before this (warm → transcribe)
        self.decoder: Callable[..., Any] | None = None    # audio file → samples (tests)
        self.factory = factory
        self._model: Any = None
        self._loaded_on = ""
        self._last_used = 0.0
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _ensure(self) -> Any:
        if self._model is not None:
            return self._model
        if not self.model_path.exists():
            raise FileNotFoundError(f"whisper model not found at {self.model_path}")
        t0 = time.perf_counter()
        try:
            self._model = self.factory(str(self.model_path), self.device, self.compute_type)
            self._loaded_on = f"{self.device}/{self.compute_type}"
        except Exception as e:
            if self.device == "cpu":
                raise
            _log.warning(f"whisper on {self.device} failed ({type(e).__name__}: {e}); "
                         "falling back to cpu/int8", extra={"action": "voice.load"})
            self._model = self.factory(str(self.model_path), "cpu", "int8")
            self._loaded_on = "cpu/int8"
        self._load_seconds = time.perf_counter() - t0
        _log.info(f"whisper loaded on {self._loaded_on} in {self._load_seconds:.1f}s",
                  extra={"action": "voice.load", "status": "ok"})
        return self._model

    def unload_if_idle(self, now: float | None = None) -> bool:
        with self._lock:
            if self._model is None:
                return False
            now = now or time.time()
            if now < self._held_until:
                return False                # a voice note is between warm-up and transcribe
            if now - self._last_used < self.idle_limit():
                return False
            self._model = None
            _log.info("whisper unloaded (idle)", extra={"action": "voice.unload"})
            return True

    def _run(self, model: Any, audio: Any, language: str | None) -> tuple[str, Any, float]:
        segs, info = self._decode(model, audio, language, vad=True)
        if not segs:
            # VAD can drop a very short command entirely — retry on the raw audio.
            segs, info = self._decode(model, audio, language, vad=False)
        text = " ".join(s.text.strip() for s in segs).strip()
        if segs:
            mean_lp = sum(s.avg_logprob for s in segs) / len(segs)
            speech = 1 - max(s.no_speech_prob for s in segs)
            conf = max(0.0, min(1.0, math.exp(mean_lp) * speech))
        else:
            conf = 0.0
        return text, info, conf

    def _decode(self, model: Any, audio: Any, language: str | None,
                vad: bool) -> tuple[list[Any], Any]:
        kwargs: dict[str, Any] = {"language": language, "beam_size": self.beam_size,
                                  "vad_filter": vad, "condition_on_previous_text": False,
                                  "initial_prompt": self.initial_prompt}
        hot = ", ".join(w for w in (self.owner_words, self.hotwords) if w)
        if hot:
            kwargs["hotwords"] = hot
        if vad:
            kwargs["vad_parameters"] = {"speech_pad_ms": self.vad_speech_pad_ms}
        segments, info = model.transcribe(audio, **kwargs)
        return list(segments), info

    def _owner_language(self, model: Any, audio: Any) -> tuple[str, float] | None:
        """V1.1: the owner speaks Bangla or English — pick between those two only.
        Free detection said Hindi for Banglish and wrote Devanagari ("ओपन करो"), and the
        retry decoded everything twice."""
        if not hasattr(model, "detect_language"):
            return None
        vad = importlib.import_module("faster_whisper.vad").VadOptions(
            speech_pad_ms=self.vad_speech_pad_ms)
        lang, prob, ranked = model.detect_language(audio, vad_filter=True, vad_parameters=vad)
        if lang in OWNER_LANGUAGES:
            return lang, float(prob)
        probs = dict(ranked)
        bn, en = float(probs.get("bn", 0.0)), float(probs.get("en", 0.0))
        # an Indian-language guess is the owner's Bangla accent, not English
        if lang in INDIC or bn >= en:
            return "bn", bn
        return "en", en

    def transcribe_sync(self, audio: Path) -> Transcript:
        t0 = time.perf_counter()
        with self._lock:
            model = self._ensure()
            self.last_load_seconds = self._load_seconds
            self._load_seconds = 0.0
            if hasattr(model, "detect_language"):
                return self._transcribe_owner(model, audio, t0)
            try:
                text, info, conf = self._run(model, str(audio), None)
            except RuntimeError as e:
                # CUDA libraries (cuBLAS/cuDNN) are loaded lazily on first use, so a
                # missing GPU runtime shows up here, not at load time.
                if not self._loaded_on.startswith("cuda"):
                    raise
                _log.warning(f"whisper GPU run failed ({e}); falling back to cpu/int8",
                             extra={"action": "voice.fallback"})
                self._model = model = self.factory(str(self.model_path), "cpu", "int8")
                self._loaded_on = "cpu/int8"
                text, info, conf = self._run(model, str(audio), None)
            lang, prob = info.language, float(info.language_probability)
            if lang not in OWNER_LANGUAGES:
                bn_text, bn_info, bn_conf = self._run(model, str(audio), "bn")
                if bn_conf >= conf:
                    text, conf, lang, prob = bn_text, bn_conf, "bn", float(
                        bn_info.language_probability)
            self._last_used, self._held_until = time.time(), 0.0
        return Transcript(text, lang, prob, conf, float(info.duration),
                          time.perf_counter() - t0, self._loaded_on)

    def _transcribe_owner(self, model: Any, audio: Path, t0: float) -> Transcript:
        """Real faster-whisper: decode the audio once, choose Bangla/English, one pass."""
        decode = self.decoder or importlib.import_module("faster_whisper").decode_audio
        samples = decode(str(audio), sampling_rate=16000)
        try:
            chosen = self._owner_language(model, samples)
            assert chosen is not None
            lang, prob = chosen
            text, info, conf = self._run(model, samples, lang)
        except RuntimeError as e:
            if not self._loaded_on.startswith("cuda"):
                raise
            _log.warning(f"whisper GPU run failed ({e}); falling back to cpu/int8",
                         extra={"action": "voice.fallback"})
            self._model = model = self.factory(str(self.model_path), "cpu", "int8")
            self._loaded_on = "cpu/int8"
            lang, prob = self._owner_language(model, samples) or ("bn", 0.0)
            text, info, conf = self._run(model, samples, lang)
        self._last_used, self._held_until = time.time(), 0.0
        return Transcript(text, lang, prob, conf, float(info.duration),
                          time.perf_counter() - t0, self._loaded_on)

    def warm_sync(self) -> float:
        """Load now (seconds it took; 0 when already loaded) — started while the voice
        note is still downloading."""
        with self._lock:
            # the transcription that follows must find the model still loaded
            self._held_until = time.time() + HOLD_SECONDS
            if self._model is not None:
                return 0.0
            self._ensure()
            self._last_used = time.time()
            return self._load_seconds

    async def warm(self) -> float:
        return await asyncio.to_thread(self.warm_sync)

    def idle_limit(self) -> float:
        """Warm-idle (V1.1): stay loaded between close voice commands, but give the
        memory back sooner when the PC is short of it (Resource Manager level)."""
        level = self.pressure() if self.pressure is not None else "normal"
        # live 2026-10-23: at "critical" (Premiere open, RAM 91 %) a 0 s limit unloaded
        # Whisper right after its 31 s warm-up — the next voice note paid it twice.
        # A short minimum keeps back-to-back commands fast.
        if level == "critical":
            return min(self.idle_unload_seconds, MIN_WARM_SECONDS)
        if level == "high":
            return min(self.idle_unload_seconds, 2 * MIN_WARM_SECONDS)
        return self.idle_unload_seconds

    async def relieve(self) -> None:
        """Resource Manager critical action `unload_idle_whisper`."""
        await asyncio.to_thread(self.unload_if_idle)

    async def transcribe(self, audio: Path) -> Transcript:
        return await asyncio.to_thread(self.transcribe_sync, audio)

    async def idle_loop(self, stop: asyncio.Event, every: float = 30.0) -> None:
        while not stop.is_set():
            self.unload_if_idle()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=every)
