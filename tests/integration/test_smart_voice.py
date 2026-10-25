"""V1.2 Smart Voice Response (owner 2026-10-25): short answers spoken in full, long or
structured results as their main point + "details in Telegram", everything on request,
never "done" for an unfinished task, never a secret aloud, a newer command silences
older answers."""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

import pytest

from channels.base import IncomingMessage, OutgoingMessage
from core.config.loader import load_config
from core.notify.notifier import MessageType, Notifier
from core.voice import pc_audio
from core.voice.intake import VoiceIntake
from core.voice.ptt import PcVoice, PushToTalk
from core.voice.speech import asks_to_repeat_in_full, spoken_answer, wants_detail
from tests.integration.test_push_to_talk import FakeListener, FakeRecorder, tone
from tests.integration.test_voice import SETTINGS, FakeModel, fake_download, make, voice_msg

PC_STATUS = ("CPU: 15%\nRAM: 73% (11.0/15.1 GB)\nDisk free: 82/109 GB (D:)\n"
             "GPU: NVIDIA GeForce RTX 5060 Ti — 100%, VRAM 13.4/15.9 GB, 55°C\n"
             "PC uptime: 13h 52m\nDrives: C: 69.3 GB free of 510.4 GB, D: 82.4 GB free")
REPORT = ("বাজার বিশ্লেষণ শেষ হয়েছে। মোট ১২টি প্রতিযোগীর মধ্যে ৩টি সবচেয়ে বেশি দামে বিক্রি "
          "করছে। গড় দাম গত মাসের চেয়ে ৮% বেড়েছে। ছোট ব্যবসায়ীরা অনলাইনে বেশি ঝুঁকছে। "
          "বড় দোকানগুলো ছাড় দিচ্ছে কম। পরের ত্রৈমাসিকে দাম স্থির থাকার সম্ভাবনা বেশি। "
          "সুপারিশ: স্টক আগেভাগে কেনা, অনলাইন বিজ্ঞাপন বাড়ানো, এবং ছাড়ের সময় বদলানো।")
CODE = ("Tests pass: 42 passed.\n```python\ndef add(a, b):\n    return a + b\n```\n"
        "Changed files: calc.py, test_calc.py")
DONE = re.compile(r"done|complete|finish|success|সম্পন্ন|শেষ হয়েছে|সফল", re.IGNORECASE)


def test_a_short_answer_is_spoken_whole() -> None:
    s = spoken_answer("বাংলাদেশের রাজধানী ঢাকা।", said_in="রাজধানী কী?")
    assert s.full and s.text.startswith("বাংলাদেশের রাজধানী ঢাকা")


def test_pc_status_gives_its_main_figures_and_points_to_telegram() -> None:
    s = spoken_answer(PC_STATUS, completed=True, said_in="পিসির অবস্থা বলো")
    assert s.text == "CPU 15%, RAM 73%। বিস্তারিত Telegram-এ দিয়েছি।" and not s.full


def test_a_long_report_is_one_or_two_sentences() -> None:
    s = spoken_answer(REPORT, completed=True, said_in="বাজার নিয়ে রিসার্চ করো")
    body = s.text.replace("বিস্তারিত Telegram-এ দিয়েছি।", "")
    assert body.count("।") <= 2 and len(s.text) < len(REPORT) / 2
    assert s.text.endswith("বিস্তারিত Telegram-এ দিয়েছি।")


def test_code_is_never_read_aloud() -> None:
    s = spoken_answer(CODE, completed=True, said_in="calculator er test likho")
    assert "def add" not in s.text and "return" not in s.text and "Tests pass" in s.text


def test_asked_for_details_reads_it_all() -> None:
    s = spoken_answer(REPORT, completed=True, detail=True, said_in="পুরোটা পড়ে শোনাও")
    assert s.full and "সুপারিশ" in s.text and "Telegram" not in s.text
    cut = spoken_answer(REPORT * 3, completed=True, detail=True, max_chars=300)
    assert not cut.full and cut.text.endswith("বাকিটা Telegram-এ আছে।")


@pytest.mark.parametrize("reason", ["", "Build finished successfully",
                                    "ফাইলটা পাওয়া যায়নি: report.docx নেই"])
def test_an_unfinished_task_is_never_called_done(reason: str) -> None:
    s = spoken_answer(reason, completed=False, said_in="রিপোর্ট বানাও")
    assert s.text.startswith("কাজটা শেষ করা যায়নি")
    assert not DONE.search(s.text.replace("শেষ করা যায়নি", ""))


@pytest.mark.parametrize("text", [
    "Account ready. password: Hx9#kq22",
    "Your key is sk-ant-" + "b" * 30,
    "Paid with 4111 1111 1111 1111 today",
])
def test_secrets_are_never_spoken(text: str) -> None:
    s = spoken_answer(text, completed=True, detail=True)
    assert s.hidden
    for secret in ("Hx9#kq22", "sk-ant-", "4111"):
        assert secret not in s.text


@pytest.mark.parametrize(("text", "detail", "repeat"), [
    ("পুরোটা পড়ে শোনাও", True, True),
    ("পুরোটা আবার একটু পড়ে শোনাও", True, True),
    ("বিস্তারিত বলো", True, True),
    ("puro ta bolo", True, True),
    ("read it all", True, True),
    ("tell me the details please", True, True),
    ("পিসির অবস্থা বিস্তারিত বলো", True, False),     # a new request, told in detail
    ("পিসির অবস্থা বলো", False, False),
    ("ফাইলটা পুরো মুছে দাও", False, False),
])
def test_detail_requests(text: str, detail: bool, repeat: bool) -> None:
    assert wants_detail(text) is detail
    assert asks_to_repeat_in_full(text) is repeat


class Here:
    def __init__(self) -> None:
        self.said: list[str] = []

    async def __call__(self, text: str) -> None:
        self.said.append(text)


async def _settle() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


def _intake(tmp_path: Path, heard: str, handler: object) -> VoiceIntake:
    t, _ = make(tmp_path, FakeModel((heard, "bn", -0.05)))
    return VoiceIntake(t, SETTINGS, tmp_path / "audio", handler)   # type: ignore[arg-type]


def test_long_chat_answer_summarised_then_read_in_full_on_request(tmp_path: Path) -> None:
    routed: list[str] = []

    async def answer(msg: IncomingMessage) -> OutgoingMessage:
        routed.append(msg.text)
        return OutgoingMessage(REPORT)
    vi = _intake(tmp_path, "বাজার নিয়ে বলো", answer)
    here = Here()

    async def go() -> None:
        out = await vi.handle(voice_msg(), fake_download, here=here)
        assert out.text.endswith(REPORT)                 # the chat gets everything
        await _settle()
        vi.transcriber = make(tmp_path, FakeModel(("পুরোটা পড়ে শোনাও", "bn", -0.05)))[0]
        again = await vi.handle(voice_msg(), fake_download, here=here)
        assert "আগের উত্তরটা পুরো পড়ে শোনাচ্ছি" in again.text
        await _settle()
    asyncio.run(go())
    assert routed == ["বাজার নিয়ে বলো"]                  # "read it all" is not a new task
    assert here.said[0].endswith("বিস্তারিত Telegram-এ দিয়েছি।")
    assert "সুপারিশ" in here.said[1] and "Telegram" not in here.said[1]


def test_task_answer_and_a_newer_command_silences_the_older_one(tmp_path: Path) -> None:
    ids = iter([21, 22])

    async def make_task(msg: IncomingMessage) -> OutgoingMessage:
        return OutgoingMessage("📥 Task", task_id=next(ids))
    vi = _intake(tmp_path, "পিসির অবস্থা বলো", make_task)

    async def go() -> None:
        await vi.handle(voice_msg(), fake_download)      # task 21
        await vi.handle(voice_msg(), fake_download)      # task 22 — a newer command
    asyncio.run(go())
    assert vi.task_speech(21, PC_STATUS, completed=True) is None
    assert vi.task_speech(22, PC_STATUS, completed=True) == (
        "CPU 15%, RAM 73%। বিস্তারিত Telegram-এ দিয়েছি।")
    assert vi.last_full["555"] == PC_STATUS              # "পুরোটা পড়ে শোনাও" can follow
    assert vi.task_speech(23, "", completed=False) == "কাজটা শেষ করা যায়নি"


def test_one_short_progress_line_for_a_slow_voice_task(tmp_path: Path) -> None:
    async def make_task(msg: IncomingMessage) -> OutgoingMessage:
        return OutgoingMessage("👂 শুনেছি — এখন 🔎 খুঁজে দেখছি\n📥 Task #5", task_id=5)
    vi = _intake(tmp_path, "আজকের খবর খুঁজে দেখো", make_task)
    vi.progress_after = 0.05
    here = Here()

    async def go() -> None:
        await vi.handle(voice_msg(), fake_download, here=here)
        await asyncio.sleep(0.2)
        await _settle()
    asyncio.run(go())
    assert here.said == ["👂 শুনেছি — এখন 🔎 খুঁজে দেখছি কাজ চলছে — শেষ হলে জানাব।"]


def test_a_quick_task_gets_no_progress_line(tmp_path: Path) -> None:
    async def make_task(msg: IncomingMessage) -> OutgoingMessage:
        return OutgoingMessage("📥 Task #6", task_id=6)
    vi = _intake(tmp_path, "volume koto", make_task)
    vi.progress_after = 0.05
    here = Here()

    async def go() -> None:
        await vi.handle(voice_msg(), fake_download, here=here)
        vi.finish(6)                                     # done before the progress time
        await asyncio.sleep(0.2)
    asyncio.run(go())
    assert here.said == []


def test_approval_is_told_once_in_voice(tmp_path: Path) -> None:
    async def make_task(msg: IncomingMessage) -> OutgoingMessage:
        return OutgoingMessage("📥 Task #8", task_id=8)
    vi = _intake(tmp_path, "ফাইলটা মুছে দাও", make_task)
    asyncio.run(vi.handle(voice_msg(), fake_download))
    assert vi.notice_speech(8, approval=True) == "এই কাজে আপনার অনুমোদন লাগবে — Telegram-এ দেখুন।"
    assert vi.notice_speech(8, approval=True) is None    # not again
    assert vi.notice_speech(99, approval=True) is None   # not a voice task


def test_notifier_tells_the_voice_when_a_task_needs_the_owner() -> None:
    seen: list[tuple[MessageType, int]] = []

    async def send(chat: str, out: OutgoingMessage) -> None:
        pass

    async def hook(chat: str, kind: MessageType, task_id: int) -> None:
        seen.append((kind, task_id))

    async def go() -> None:
        n = Notifier(load_config().default.notification_throttle, send)
        n.on_task_notice.append(hook)
        await n.notify("1", MessageType.APPROVAL_REQUIRED, "🔐 approve?", task_id=4)
        await n.notify("1", MessageType.PROGRESS, "step", task_id=4)
        await n.notify("1", MessageType.APPROVAL_REQUIRED, "no task")
        await n.wait_hooks()
    asyncio.run(go())
    assert seen == [(MessageType.APPROVAL_REQUIRED, 4)]


class FakePlayer:
    def __init__(self) -> None:
        self.played: list[bytes] = []
        self.stops = 0

    async def play(self, audio: bytes) -> None:
        await asyncio.sleep(0.2)
        self.played.append(audio)

    def stop(self) -> None:
        self.stops += 1


class FakeSpeaker:
    async def synthesize(self, text: str) -> bytes:
        return text.encode()


def test_pressing_the_key_silences_nova_and_drops_queued_answers() -> None:
    async def go() -> FakePlayer:
        player = FakePlayer()
        pv = PcVoice(FakeSpeaker(), player, asyncio.get_running_loop())
        jobs = [asyncio.create_task(pv.say(t)) for t in ("প্রথম", "দ্বিতীয়")]
        await asyncio.sleep(0.05)
        pv.interrupt()
        await asyncio.gather(*jobs, return_exceptions=True)
        await pv.say("নতুন উত্তর")                      # a later answer still plays
        return player
    player = asyncio.run(go())
    assert player.stops == 1 and player.played == ["নতুন উত্তর".encode()]


def test_key_down_interrupts_before_anything_else() -> None:
    events: list[str] = []
    loop = asyncio.new_event_loop()

    async def on_audio(wav: bytes, seconds: float) -> None:
        pass
    p = PushToTalk(pc_audio_settings(), loop, on_audio, recorder=FakeRecorder(tone(1)),
                   listener=FakeListener, cue=events.append,
                   interrupt=lambda: events.append("silence"))
    p.down()
    loop.close()
    assert events[0] == "silence"


def pc_audio_settings():  # type: ignore[no-untyped-def]
    from core.config.schema import PushToTalkSection
    return PushToTalkSection(enabled=True)


@pytest.mark.skipif(not hasattr(pc_audio, "Player"), reason="Windows")
def test_real_player_plays_silence_and_stops_at_once(tmp_path: Path) -> None:
    """Real winsound, silent audio (nothing is heard): plays for its length, stops on
    cancel, leaves no file behind."""
    import sys
    if sys.platform != "win32":
        pytest.skip("Windows")
    silence = pc_audio.to_wav(b"\x00\x00" * 16000 * 2)      # 2 s

    async def go() -> tuple[float, float]:
        player = pc_audio.Player(tmp_path / "say")
        t0 = time.perf_counter()
        await player.play(silence)
        whole = time.perf_counter() - t0
        job = asyncio.create_task(player.play(silence))
        await asyncio.sleep(0.3)
        t1 = time.perf_counter()
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)
        return whole, time.perf_counter() - t1
    whole, stopped = asyncio.run(go())
    assert 1.9 < whole < 3.5 and stopped < 0.3
    assert list((tmp_path / "say").glob("*")) == []
