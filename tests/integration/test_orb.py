"""V1.2 floating light (plan §65 V1.2-5): red while Nova listens, amber while it thinks,
blue while it speaks, gone when idle — and on Windows a real window that never takes the
focus, lets clicks through and is never in a screenshot."""

from __future__ import annotations

import asyncio
import sys
import time

import pytest

from core.config.schema import PushToTalkSection
from core.voice.orb import IDLE, LISTENING, SPEAKING, THINKING, THINKING_SECONDS, Orb
from core.voice.ptt import PcVoice, PushToTalk
from tests.integration.test_push_to_talk import FakeListener, FakeRecorder, tone


class Seen:
    def __init__(self) -> None:
        self.states: list[str] = []

    def show(self, state: str) -> None:
        self.states.append(state)

    def close(self) -> None:
        pass


def _orb() -> tuple[Orb, Seen, list[float]]:
    clock = [0.0]
    seen = Seen()
    return Orb(seen, clock=lambda: clock[0]), seen, clock


def test_a_spoken_turn_lights_listening_thinking_speaking_then_out() -> None:
    orb, seen, _ = _orb()
    orb.listening()
    orb.thinking()
    orb.speech_started()
    orb.speech_ended()
    assert seen.states == [LISTENING, THINKING, SPEAKING, IDLE]


def test_a_new_press_wins_and_speech_keeps_the_light_on() -> None:
    orb, seen, _ = _orb()
    orb.speech_started()
    orb.listening()                       # the owner speaks over Nova
    orb.interrupted()
    orb.speech_ended()                    # the cut-off answer ends: still listening
    assert orb.state == LISTENING
    orb.thinking()
    orb.speech_started()
    orb.speech_started()                  # two answers queued
    orb.speech_ended()
    assert orb.state == SPEAKING
    orb.idle()                            # a task ends while the other one speaks
    assert orb.state == SPEAKING
    orb.speech_ended()
    assert orb.state == IDLE


def test_thinking_that_leads_to_nothing_fades_out() -> None:
    orb, _seen, clock = _orb()
    orb.listening()
    orb.thinking()
    orb.tick()
    assert orb.state == THINKING
    clock[0] += THINKING_SECONDS + 1
    orb.tick()
    assert orb.state == IDLE


def test_a_broken_view_never_breaks_voice() -> None:
    class Broken:
        def show(self, state: str) -> None:
            raise RuntimeError("no screen")

        def close(self) -> None:
            pass
    orb = Orb(Broken())
    orb.listening()
    assert orb.state == LISTENING


def test_push_to_talk_and_the_pc_voice_drive_the_light() -> None:
    orb, seen, _ = _orb()
    loop = asyncio.new_event_loop()

    async def on_audio(wav: bytes, seconds: float) -> None:
        pass
    p = PushToTalk(PushToTalkSection(enabled=True), loop, on_audio,
                   recorder=FakeRecorder(tone(1.5)), listener=FakeListener, cue=None, orb=orb)
    p.down()
    p.up()
    assert seen.states == [LISTENING, THINKING]

    class Speaker:
        async def stream(self, text: str):  # type: ignore[no-untyped-def]
            yield b"x"

    class Player:
        async def play(self, audio: bytes) -> None:
            assert orb.state == SPEAKING

        def stop(self) -> None:
            pass
    voice = PcVoice(Speaker(), Player(), loop, orb)
    loop.run_until_complete(voice.say("হ্যালো"))
    for f in p.turns:
        loop.run_until_complete(asyncio.wrap_future(f, loop=loop))
    loop.close()
    assert seen.states == [LISTENING, THINKING, SPEAKING, IDLE]
    tap, tap_seen, _ = _orb()
    loop2 = asyncio.new_event_loop()
    p2 = PushToTalk(PushToTalkSection(enabled=True), loop2, on_audio,
                    recorder=FakeRecorder(tone(0.1)), listener=FakeListener, cue=None, orb=tap)
    p2.down()
    p2.up()                               # too short: the light goes out at once
    loop2.close()
    assert tap.state == IDLE


@pytest.mark.skipif(sys.platform != "win32", reason="the real window is Windows only")
def test_the_real_light_takes_no_focus_lets_clicks_through_and_is_never_captured() -> None:
    import ctypes
    import ctypes.wintypes

    from PIL import ImageGrab

    from core.voice import orb as orb_mod
    user32 = ctypes.windll.user32
    before = user32.GetForegroundWindow()
    view = orb_mod.TkView()
    assert not view.error and view.hwnd
    try:
        view.show(LISTENING)
        time.sleep(0.6)
        user32.GetWindowLongW.restype = ctypes.c_long
        ex = user32.GetWindowLongW(view.hwnd, orb_mod.GWL_EXSTYLE)
        for bit in (orb_mod.WS_EX_NOACTIVATE, orb_mod.WS_EX_TRANSPARENT,
                    orb_mod.WS_EX_TOOLWINDOW, orb_mod.WS_EX_TOPMOST):
            assert ex & bit, hex(bit)
        affinity = ctypes.c_uint(0)
        assert user32.GetWindowDisplayAffinity(view.hwnd, ctypes.byref(affinity))
        assert affinity.value == orb_mod.WDA_EXCLUDEFROMCAPTURE
        assert user32.IsWindowVisible(view.hwnd)
        assert user32.GetForegroundWindow() == before          # the focus never moved
        # a real screenshot of the light's own place. Control first: with the exclusion
        # off the (half see-through) red light IS in the screenshot — else this proves
        # nothing; as Nova sets it, it is not.
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(view.hwnd, ctypes.byref(rect))

        def red() -> int:
            time.sleep(0.3)
            shot = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom),
                                  all_screens=True).convert("RGB")
            return sum(n for n, (r, g, b) in shot.getcolors(10_000) or []
                       if r > g + 60 and r > b + 60)
        # clicks go through: Windows names the window UNDER the light at its centre
        centre = ctypes.wintypes.POINT((rect.left + rect.right) // 2,
                                       (rect.top + rect.bottom) // 2)
        user32.WindowFromPoint.argtypes = [ctypes.wintypes.POINT]
        under = user32.WindowFromPoint(centre)
        assert view.hwnd not in (under, user32.GetAncestor(under, 2))   # GA_ROOT
        user32.SetWindowDisplayAffinity(view.hwnd, 0)
        seen = red()
        user32.SetWindowDisplayAffinity(view.hwnd, orb_mod.WDA_EXCLUDEFROMCAPTURE)
        hidden = red()
        assert seen > 300 and hidden < 20, (seen, hidden)
    finally:
        view.close()
