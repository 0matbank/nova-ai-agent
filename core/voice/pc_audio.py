"""V1.2 push-to-talk on the PC, with Windows' own parts only (no new library):
the microphone through winmm (waveIn), a global shortcut through RegisterHotKey, and
the spoken answer through winsound. The microphone is open only while the shortcut is
held — never always on (plan §৬৯-৩)."""

from __future__ import annotations

import contextlib
import ctypes
import functools
import io
import sys
import threading
import time
import wave
from collections.abc import Callable
from ctypes import wintypes
from typing import Any

from core.log import get_logger

_log = get_logger("core")

RATE = 16000                       # what Whisper wants: 16 kHz mono 16-bit
CHUNK_SECONDS = 0.1
WHDR_DONE = 0x1
WAVE_MAPPER = 0xFFFFFFFF
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
MOD = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}
MOD_NOREPEAT = 0x4000
VK_MOD = {"alt": 0x12, "ctrl": 0x11, "control": 0x11, "shift": 0x10, "win": 0x5B}
VK = {"space": 0x20, "pause": 0x13, "scrolllock": 0x91, "insert": 0x2D, "home": 0x24,
      "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "capslock": 0x14,
      **{f"f{n}": 0x6F + n for n in range(1, 25)},
      **{chr(c): c for c in range(ord("0"), ord("9") + 1)},
      **{chr(c).lower(): c for c in range(ord("A"), ord("Z") + 1)}}


class AudioError(RuntimeError):
    pass


def parse_hotkey(text: str) -> tuple[int, int, list[int]]:
    """"ctrl+alt+space" → (modifier flags, key code, codes that must stay held)."""
    parts = [p.strip().lower() for p in text.split("+") if p.strip()]
    if not parts or parts[-1] not in VK or any(p not in MOD for p in parts[:-1]):
        raise ValueError(f"unknown shortcut: {text!r}")
    mods = 0
    for p in parts[:-1]:
        mods |= MOD[p]
    return mods, VK[parts[-1]], [VK[parts[-1]]]


def to_wav(pcm: bytes, rate: int = RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def decode_to_wav(audio: bytes, rate: int = 24000) -> bytes:
    """The spoken answer (MP3 from edge, OGG/Opus from Gemini) → WAV winsound can play."""
    import av  # already a dependency (faster-whisper)
    pcm = bytearray()
    with av.open(io.BytesIO(audio), mode="r") as container:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
        for frame in container.decode(audio=0):
            for out in resampler.resample(frame):
                pcm += bytes(out.planes[0])[: out.samples * 2]
        for out in resampler.resample(None):
            pcm += bytes(out.planes[0])[: out.samples * 2]
    return to_wav(bytes(pcm), rate)


def play(audio: bytes) -> None:
    """Play a spoken answer on the PC speakers; blocks until it ends."""
    import winsound
    winsound.PlaySound(decode_to_wav(audio), winsound.SND_MEMORY | winsound.SND_NODEFAULT)


def cue(kind: str) -> None:
    """A short tone: listening started / stopped."""
    import winsound
    with contextlib.suppress(RuntimeError):     # no sound device: the cue is optional
        winsound.Beep(880 if kind == "start" else 660, 70)


class _WaveFormat(ctypes.Structure):
    _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD),
                ("nSamplesPerSec", wintypes.DWORD), ("nAvgBytesPerSec", wintypes.DWORD),
                ("nBlockAlign", wintypes.WORD), ("wBitsPerSample", wintypes.WORD),
                ("cbSize", wintypes.WORD)]


class _WaveHdr(ctypes.Structure):
    _fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", wintypes.DWORD),
                ("dwBytesRecorded", wintypes.DWORD), ("dwUser", ctypes.c_size_t),
                ("dwFlags", wintypes.DWORD), ("dwLoops", wintypes.DWORD),
                ("lpNext", ctypes.c_void_p), ("reserved", ctypes.c_size_t)]


@functools.cache
def _winmm() -> Any:
    mm = ctypes.WinDLL("winmm")
    handle, hdr, uint = wintypes.HANDLE, ctypes.POINTER(_WaveHdr), wintypes.UINT
    mm.waveInOpen.argtypes = [ctypes.POINTER(handle), uint, ctypes.POINTER(_WaveFormat),
                              ctypes.c_size_t, ctypes.c_size_t, wintypes.DWORD]
    for name in ("waveInPrepareHeader", "waveInUnprepareHeader", "waveInAddBuffer"):
        getattr(mm, name).argtypes = [handle, hdr, uint]
    for name in ("waveInStart", "waveInStop", "waveInReset", "waveInClose"):
        getattr(mm, name).argtypes = [handle]
    mm.waveInGetErrorTextW.argtypes = [uint, wintypes.LPWSTR, uint]
    return mm


class WaveInRecorder:
    """The default microphone, 16 kHz mono, while `start()`…`stop()`."""

    def __init__(self, max_seconds: float = 60, buffers: int = 8) -> None:
        self.max_bytes = int(max_seconds * RATE * 2)
        self.buffers = buffers
        self._handle: Any = None
        self._hdrs: list[_WaveHdr] = []
        self._data: list[Any] = []
        self._pcm = bytearray()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    @staticmethod
    def _winmm() -> Any:
        return _winmm()

    def _check(self, rc: int, what: str) -> None:
        if rc != 0:
            text = ctypes.create_unicode_buffer(256)
            self._winmm().waveInGetErrorTextW(rc, text, 256)
            raise AudioError(f"{what}: {text.value or rc}")

    def start(self) -> None:
        mm = self._winmm()
        fmt = _WaveFormat(1, 1, RATE, RATE * 2, 2, 16, 0)
        handle = wintypes.HANDLE()
        self._check(mm.waveInOpen(ctypes.byref(handle), WAVE_MAPPER, ctypes.byref(fmt),
                                  0, 0, 0), "microphone")
        self._handle, self._pcm, self._hdrs, self._data = handle, bytearray(), [], []
        size = int(RATE * 2 * CHUNK_SECONDS)
        for _ in range(self.buffers):
            data = ctypes.create_string_buffer(size)
            hdr = _WaveHdr(ctypes.cast(data, ctypes.c_void_p), size, 0, 0, 0, 0, None, 0)
            self._check(mm.waveInPrepareHeader(handle, ctypes.byref(hdr),
                                               ctypes.sizeof(hdr)), "prepare")
            self._check(mm.waveInAddBuffer(handle, ctypes.byref(hdr), ctypes.sizeof(hdr)),
                        "buffer")
            self._hdrs.append(hdr)
            self._data.append(data)
        self._stop.clear()
        self._check(mm.waveInStart(handle), "start")
        self._thread = threading.Thread(target=self._collect, name="nova-mic", daemon=True)
        self._thread.start()

    def _take(self, hdr: _WaveHdr, data: Any) -> None:
        with self._lock:
            room = self.max_bytes - len(self._pcm)
            if room > 0:
                self._pcm += data.raw[: min(hdr.dwBytesRecorded, room)]

    def _collect(self) -> None:
        mm = self._winmm()
        while not self._stop.is_set():
            for hdr, data in zip(self._hdrs, self._data, strict=True):
                if hdr.dwFlags & WHDR_DONE:
                    self._take(hdr, data)
                    hdr.dwFlags &= ~WHDR_DONE
                    hdr.dwBytesRecorded = 0
                    mm.waveInAddBuffer(self._handle, ctypes.byref(hdr), ctypes.sizeof(hdr))
            time.sleep(0.02)

    def stop(self) -> bytes:
        """Close the microphone and return what was heard (PCM16)."""
        if self._handle is None:
            return b""
        mm = self._winmm()
        self._stop.set()
        if self._thread is not None:
            self._thread.join(2)
        mm.waveInStop(self._handle)
        mm.waveInReset(self._handle)            # hands back the partly filled buffers
        for hdr, data in zip(self._hdrs, self._data, strict=True):
            if hdr.dwFlags & WHDR_DONE and hdr.dwBytesRecorded:
                self._take(hdr, data)
            mm.waveInUnprepareHeader(self._handle, ctypes.byref(hdr), ctypes.sizeof(hdr))
        mm.waveInClose(self._handle)
        self._handle = None
        return bytes(self._pcm)


class HotkeyListener:
    """Hold the shortcut: `on_down` when pressed, `on_up` when its key is let go (or after
    `max_seconds`). RegisterHotKey — no keyboard hook, nothing else is watched."""

    def __init__(self, hotkey: str, on_down: Callable[[], None], on_up: Callable[[], None],
                 max_seconds: float = 60) -> None:
        self.mods, self.vk, self.held = parse_hotkey(hotkey)
        self.hotkey = hotkey
        self.on_down, self.on_up = on_down, on_up
        self.max_seconds = max_seconds
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self.error: str | None = None

    def start(self) -> bool:
        if sys.platform != "win32":
            self.error = "not Windows"
            return False
        self._thread = threading.Thread(target=self._run, name="nova-ptt", daemon=True)
        self._thread.start()
        self._ready.wait(5)
        return self.error is None

    def _held(self, user32: Any) -> bool:
        return all(user32.GetAsyncKeyState(vk) & 0x8000 for vk in self.held)

    def _run(self) -> None:
        user32 = ctypes.WinDLL("user32")
        self._thread_id = ctypes.WinDLL("kernel32").GetCurrentThreadId()
        if not user32.RegisterHotKey(None, 1, self.mods | MOD_NOREPEAT, self.vk):
            self.error = f"shortcut {self.hotkey} is taken by another app"
            self._ready.set()
            return
        self._ready.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message != WM_HOTKEY:
                    continue
                self._safe(self.on_down)
                started = time.monotonic()
                while self._held(user32) and time.monotonic() - started < self.max_seconds:
                    time.sleep(0.02)
                self._safe(self.on_up)
        finally:
            user32.UnregisterHotKey(None, 1)

    @staticmethod
    def _safe(fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception:  # noqa: BLE001 - one failed turn must not end the listener
            _log.exception("push-to-talk step failed", extra={"action": "voice.ptt"})

    def stop(self) -> None:
        if self._thread is not None and self._thread.is_alive() and self._thread_id:
            ctypes.WinDLL("user32").PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(2)
