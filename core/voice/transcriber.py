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
        import nvidia  # type: ignore[import-not-found,import-untyped,unused-ignore]
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
    from faster_whisper import (
        WhisperModel,  # type: ignore[import-not-found,import-untyped,unused-ignore]
    )
    return WhisperModel(path, device=device, compute_type=compute_type)


class Transcriber:
    def __init__(self, model_path: Path, device: str = "cuda", compute_type: str = "float16",
                 idle_unload_seconds: float = 300, beam_size: int = 5,
                 factory: ModelFactory = _default_factory, hint_words: list[str] | None = None,
                 vad_speech_pad_ms: int = 400) -> None:
        self.model_path = model_path
        self.device = device
        self.compute_type = compute_type
        self.idle_unload_seconds = idle_unload_seconds
        self.beam_size = beam_size
        # Vocabulary bias (faster-whisper initial_prompt) for the owner's usual words.
        self.initial_prompt = ", ".join(hint_words) if hint_words else None
        self.vad_speech_pad_ms = vad_speech_pad_ms
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
        _log.info(f"whisper loaded on {self._loaded_on}", extra={"action": "voice.load",
                                                                "status": "ok"})
        return self._model

    def unload_if_idle(self, now: float | None = None) -> bool:
        with self._lock:
            if self._model is None:
                return False
            if (now or time.time()) - self._last_used < self.idle_unload_seconds:
                return False
            self._model = None
            _log.info("whisper unloaded (idle)", extra={"action": "voice.unload"})
            return True

    def _run(self, model: Any, audio: str, language: str | None) -> tuple[str, Any, float]:
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

    def _decode(self, model: Any, audio: str, language: str | None,
                vad: bool) -> tuple[list[Any], Any]:
        kwargs: dict[str, Any] = {"language": language, "beam_size": self.beam_size,
                                  "vad_filter": vad, "condition_on_previous_text": False,
                                  "initial_prompt": self.initial_prompt}
        if vad:
            kwargs["vad_parameters"] = {"speech_pad_ms": self.vad_speech_pad_ms}
        segments, info = model.transcribe(audio, **kwargs)
        return list(segments), info

    def transcribe_sync(self, audio: Path) -> Transcript:
        t0 = time.perf_counter()
        with self._lock:
            model = self._ensure()
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
            self._last_used = time.time()
        return Transcript(text, lang, prob, conf, float(info.duration),
                          time.perf_counter() - t0, self._loaded_on)

    async def transcribe(self, audio: Path) -> Transcript:
        return await asyncio.to_thread(self.transcribe_sync, audio)

    async def idle_loop(self, stop: asyncio.Event, every: float = 30.0) -> None:
        while not stop.is_set():
            self.unload_if_idle()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=every)
