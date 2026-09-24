"""Phase 9: voice → transcript → same path as text; unclear speech needs ✅."""

from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from channels.base import IncomingCallback, IncomingMessage, OutgoingMessage, VoiceRef
from core.config.schema import VoiceSection
from core.voice.intake import VoiceIntake
from core.voice.transcriber import Transcriber

SETTINGS = VoiceSection(max_duration_seconds=300, confirm_below_confidence=0.85,
                        confirm_timeout_seconds=300, keep_audio=False, beam_size=5)


@dataclass
class Seg:
    text: str
    avg_logprob: float
    no_speech_prob: float = 0.01


@dataclass
class Info:
    language: str
    language_probability: float
    duration: float = 3.0


class FakeModel:
    """Scripted faster-whisper model: language=None → auto result, "bn" → forced."""

    def __init__(self, auto: tuple[str, str, float], forced_bn: tuple[str, float] | None = None
                 ) -> None:
        self.auto = auto
        self.forced_bn = forced_bn
        self.calls: list[dict[str, Any]] = []

    def transcribe(self, audio: str, **kw: Any) -> tuple[list[Seg], Info]:
        self.calls.append({"audio": audio, **kw})
        assert Path(audio).exists()
        if kw.get("language") == "bn" and self.forced_bn:
            text, lp = self.forced_bn
            return [Seg(text, lp)], Info("bn", 1.0)
        text, lang, lp = self.auto
        return ([Seg(text, lp)] if text else []), Info(lang, 0.9)


def make(tmp_path: Path, model: FakeModel, fail_cuda: bool = False) -> tuple[Transcriber, list]:
    (tmp_path / "model").mkdir(exist_ok=True)
    loads: list[str] = []

    def factory(path: str, device: str, compute: str) -> FakeModel:
        loads.append(f"{device}/{compute}")
        if fail_cuda and device == "cuda":
            raise RuntimeError("cuDNN not found")
        return model
    return Transcriber(tmp_path / "model", factory=factory, idle_unload_seconds=60), loads


def voice_msg(duration: int = 3) -> IncomingMessage:
    return IncomingMessage("telegram", "555", "555", "9", "", dt.datetime.now(dt.UTC),
                           VoiceRef("file-1", duration, "audio/ogg", 1000))


async def fake_download(file_id: str) -> bytes:
    return b"OggS fake audio"


class Echo:
    def __init__(self) -> None:
        self.got: list[str] = []

    async def __call__(self, msg: IncomingMessage) -> OutgoingMessage:
        self.got.append(msg.text)
        assert msg.voice is None
        return OutgoingMessage(f"📥 Task #1 তৈরি হয়েছে — {msg.text}")


def intake(tmp_path: Path, model: FakeModel) -> tuple[VoiceIntake, Echo]:
    t, _ = make(tmp_path, model)
    echo = Echo()
    return VoiceIntake(t, SETTINGS, tmp_path / "audio", echo), echo


def test_clear_bangla_voice_runs_like_text(tmp_path: Path) -> None:
    vi, echo = intake(tmp_path, FakeModel(("পিসির অবস্থা বলো", "bn", -0.1)))
    out = asyncio.run(vi.handle(voice_msg(), fake_download))
    assert echo.got == ["পিসির অবস্থা বলো"]
    assert "🎙️ শুনলাম (বাংলা" in out.text and "Task #1" in out.text
    assert list((tmp_path / "audio").glob("*")) == []            # audio deleted


def test_banglish_misdetected_as_hindi_is_retried_as_bangla(tmp_path: Path) -> None:
    model = FakeModel(("पीसी स्टेटस बोलो", "hi", -0.9), forced_bn=("পিসি স্ট্যাটাস বলো", -0.05))
    vi, echo = intake(tmp_path, model)
    asyncio.run(vi.handle(voice_msg(), fake_download))
    assert echo.got == ["পিসি স্ট্যাটাস বলো"]
    assert [c.get("language") for c in model.calls] == [None, "bn"]


def test_hints_and_vad_padding_passed(tmp_path: Path) -> None:
    model = FakeModel(("hello", "en", -0.05))
    (tmp_path / "model").mkdir()
    t = Transcriber(tmp_path / "model", factory=lambda *a: model,
                    hint_words=["পিসি", "স্ক্রিনশট"], vad_speech_pad_ms=400)
    asyncio.run(t.transcribe(tmp_path / "model"))
    call = model.calls[0]
    assert call["initial_prompt"] == "পিসি, স্ক্রিনশট"
    assert call["vad_parameters"] == {"speech_pad_ms": 400}


def test_vad_dropping_everything_retries_raw_audio(tmp_path: Path) -> None:
    class DropsWithVad(FakeModel):
        def transcribe(self, audio: str, **kw: Any) -> tuple[list[Seg], Info]:
            self.calls.append(kw)
            if kw["vad_filter"]:
                return [], Info("bn", 0.5)
            return [Seg("বলো", -0.05)], Info("bn", 0.9)
    model = DropsWithVad(("", "bn", 0.0))
    (tmp_path / "model").mkdir()
    r = asyncio.run(Transcriber(tmp_path / "model", factory=lambda *a: model)
                    .transcribe(tmp_path / "model"))
    assert r.text == "বলো" and [c["vad_filter"] for c in model.calls] == [True, False]


def test_unclear_voice_offers_suggestion_never_guesses(tmp_path: Path) -> None:
    t, _ = make(tmp_path, FakeModel(("পিছি বর্তমন ওস্তা বলো", "bn", -0.4)))
    echo = Echo()

    async def corrector(heard: str, lang: str) -> str:
        assert heard == "পিছি বর্তমন ওস্তা বলো" and lang == "bn"
        return "পিসির বর্তমান অবস্থা বলো"
    vi = VoiceIntake(t, SETTINGS, tmp_path / "audio", echo, corrector)
    out = asyncio.run(vi.handle(voice_msg(), fake_download))
    assert echo.got == []                                          # nothing ran
    assert "আপনি কি এটা বলতে চেয়েছেন" in out.text and "পিসির বর্তমান অবস্থা বলো" in out.text
    labels = [b[0] for row in out.buttons for b in row]
    assert labels == ["✅ হ্যাঁ, এটাই", "✏️ যা শুনেছ সেটাই", "🔁 আবার বলব"]
    yes = out.buttons[0][0][1]
    asyncio.run(vi.on_button(IncomingCallback("telegram", "555", "555", "c", yes, "1", "x")))
    assert echo.got == ["পিসির বর্তমান অবস্থা বলো"]


def test_retry_button_runs_nothing(tmp_path: Path) -> None:
    t, _ = make(tmp_path, FakeModel(("কিছু একটা", "bn", -0.6)))
    echo = Echo()

    async def corrector(heard: str, lang: str) -> str:
        return "অন্য কিছু"
    vi = VoiceIntake(t, SETTINGS, tmp_path / "audio", echo, corrector)
    out = asyncio.run(vi.handle(voice_msg(), fake_download))
    retry = out.buttons[1][1][1]
    reply = asyncio.run(vi.on_button(IncomingCallback("telegram", "555", "555", "c", retry,
                                                      "1", "x")))
    assert echo.got == [] and "আবার বলুন" in (reply.new_text or "")


def test_suggestion_cleanup() -> None:
    from core.voice.correct import _clean
    assert _clean('«পিসির অবস্থা বলো»\nextra line') == "পিসির অবস্থা বলো"
    assert _clean('"take a screenshot"') == "take a screenshot"


def test_unclear_voice_waits_for_confirmation(tmp_path: Path) -> None:
    vi, echo = intake(tmp_path, FakeModel(("ফাইলটা ডিলিট কর", "bn", -1.6)))
    out = asyncio.run(vi.handle(voice_msg(), fake_download))
    assert echo.got == []                                       # nothing ran
    assert "নিশ্চিত না হওয়া পর্যন্ত কিছু চালাব না" in out.text
    yes = out.buttons[0][0][1]
    cb = IncomingCallback("telegram", "555", "555", "c1", yes, "10", out.text)
    reply = asyncio.run(vi.on_button(cb))
    assert echo.got == ["ফাইলটা ডিলিট কর"] and "✅ নিশ্চিত" in (reply.new_text or "")
    again = asyncio.run(vi.on_button(cb))                       # single use
    assert "আগেই" in again.toast and echo.got == ["ফাইলটা ডিলিট কর"]


def test_unclear_voice_rejected(tmp_path: Path) -> None:
    vi, echo = intake(tmp_path, FakeModel(("কিছু একটা", "bn", -1.8)))
    out = asyncio.run(vi.handle(voice_msg(), fake_download))
    no = out.buttons[0][1][1]
    asyncio.run(vi.on_button(IncomingCallback("telegram", "555", "555", "c", no, "1", "x")))
    assert echo.got == []


def test_silence_and_too_long(tmp_path: Path) -> None:
    vi, echo = intake(tmp_path, FakeModel(("", "bn", -0.1)))
    assert "বুঝতে পারিনি" in asyncio.run(vi.handle(voice_msg(), fake_download)).text
    assert "লম্বা" in asyncio.run(vi.handle(voice_msg(duration=900), fake_download)).text
    assert echo.got == []


def test_cuda_failure_falls_back_to_cpu(tmp_path: Path) -> None:
    t, loads = make(tmp_path, FakeModel(("hello", "en", -0.1)), fail_cuda=True)
    r = asyncio.run(t.transcribe(tmp_path / "model"))            # any existing path
    assert loads == ["cuda/float16", "cpu/int8"] and r.device == "cpu/int8"


def test_gpu_runtime_error_at_first_use_falls_back(tmp_path: Path) -> None:
    class CudaBroken(FakeModel):
        def transcribe(self, audio: str, **kw: Any) -> tuple[list[Seg], Info]:
            raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
    good = FakeModel(("hello", "en", -0.1))
    models = iter([CudaBroken(("", "en", 0.0)), good])
    (tmp_path / "model").mkdir()
    loads: list[str] = []

    def factory(path: str, device: str, compute: str) -> FakeModel:
        loads.append(f"{device}/{compute}")
        return next(models)
    t = Transcriber(tmp_path / "model", factory=factory)
    r = asyncio.run(t.transcribe(tmp_path / "model"))
    assert r.text == "hello" and r.device == "cpu/int8"
    assert loads == ["cuda/float16", "cpu/int8"]


def test_idle_unload(tmp_path: Path) -> None:
    t, loads = make(tmp_path, FakeModel(("hello", "en", -0.1)))
    asyncio.run(t.transcribe(tmp_path / "model"))
    assert t.loaded and not t.unload_if_idle()
    assert t.unload_if_idle(now=t._last_used + 61) and not t.loaded
    asyncio.run(t.transcribe(tmp_path / "model"))
    assert loads == ["cuda/float16", "cuda/float16"]              # reloaded on demand


def test_missing_model_reported(tmp_path: Path) -> None:
    t = Transcriber(tmp_path / "nope")
    vi = VoiceIntake(t, SETTINGS, tmp_path / "audio", Echo())
    assert "পাওয়া যায়নি" in asyncio.run(vi.handle(voice_msg(), fake_download)).text


def test_telegram_voice_update_reaches_handler(tmp_path: Path) -> None:
    from channels.telegram.auth import ChatWhitelist
    from channels.telegram.channel import TelegramChannel
    from tests.mocks.telegram import OWNER, FakeTelegram

    seen: list[IncomingMessage] = []

    async def handler(msg: IncomingMessage) -> OutgoingMessage:
        seen.append(msg)
        return OutgoingMessage("ok")
    fake = FakeTelegram()
    ch = TelegramChannel(fake.api(), ChatWhitelist([str(OWNER)]), handler, poll_timeout=1)
    fake.push_message(None, extra={"voice": {"file_id": "abc", "duration": 4,
                                             "mime_type": "audio/ogg", "file_size": 999}})
    asyncio.run(ch.poll_once())
    [m] = seen
    assert m.voice is not None and m.voice.file_id == "abc" and m.voice.duration == 4


@pytest.mark.e2e
def test_real_whisper_english_speech(tmp_path: Path) -> None:
    """Local only: synthesize speech with Windows SAPI, transcribe with large-v3."""
    import shutil
    import subprocess
    import sys
    if sys.platform != "win32":
        pytest.skip("Windows SAPI")
    from core.config import load_config
    cfg = load_config()
    model = cfg.root / cfg.models.whisper.path
    if not (model / "model.bin").exists():
        pytest.skip("whisper model not downloaded")
    wav = tmp_path / "s.wav"
    ps = shutil.which("powershell")
    assert ps
    subprocess.run([ps, "-NoProfile", "-Command",  # noqa: S603
                    "Add-Type -AssemblyName System.Speech; $s=New-Object "
                    "System.Speech.Synthesis.SpeechSynthesizer; "
                    f"$s.SetOutputToWaveFile('{wav}'); $s.Speak('please show the pc status'); "
                    "$s.Dispose()"], check=True)
    t = Transcriber(model, cfg.models.whisper.device, cfg.models.whisper.compute_type)
    r = asyncio.run(t.transcribe(wav))
    assert r.language == "en" and "status" in r.text.lower(), r


@pytest.mark.parametrize("text,read_only", [
    ("Take a screenshot", True), ("পিসির অবস্থা বলো", True),
    ("বাংলাদেশের রাজধানী কি", True), ("what is python?", True),
    ("ফাইলটা ডিলিট কর", False), ("কিছু একটা", False), ("notepad kholo", False),
    ("Click TV repo te fix koro", False),
])
def test_read_only_requests(text: str, read_only: bool) -> None:
    from core.voice.intake import is_read_only_request
    assert is_read_only_request(text) is read_only


def test_misheard_but_harmless_request_runs_without_confirm(tmp_path: Path) -> None:
    vi, echo = intake(tmp_path, FakeModel(("Take a screenshot", "en", -0.33)))   # ~72 %
    out = asyncio.run(vi.handle(voice_msg(), fake_download))
    assert echo.got == ["Take a screenshot"] and out.buttons == []
