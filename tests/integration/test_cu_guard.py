"""Plan §72 CU-3b — the owner stays in charge: a real Escape stops Nova's input, Nova's
own injected keys do not; Nova does not grab the keyboard/foreground while the owner is
using the PC ("jofresh", 2026-10-24). Runs on this desktop; injects only a harmless
zero-distance mouse move and an Escape that the hook must IGNORE (it is injected)."""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "workers" / "desktop-worker"))

WIN = pytest.mark.skipif(sys.platform != "win32", reason="Windows input")


@pytest.fixture(autouse=True)
def _fresh() -> None:
    import desktop_guard
    desktop_guard.clear_halt()
    yield
    desktop_guard.clear_halt()
    with desktop_guard._lock:                 # leave no "owner active" for later tests
        desktop_guard._state.update(owner_at=0.0, mouse=None)


def test_only_a_real_escape_halts() -> None:
    import desktop_guard as g
    from desktop_ui import UIError
    assert not g.on_key(g.WM_KEYDOWN, g.VK_ESCAPE, g.LLKHF_INJECTED)   # Nova's own Esc
    assert not g.on_key(g.WM_KEYDOWN, 0x41, 0)                          # another key
    assert not g.on_key(0x0101, g.VK_ESCAPE, 0)                         # key up
    assert not g.halted()
    assert g.on_key(g.WM_KEYDOWN, g.VK_ESCAPE, 0)                       # the owner's Esc
    assert g.halted()
    with pytest.raises(UIError, match="user_cancelled"):
        g.check_halt()
    g.clear_halt()
    assert not g.halted()


@WIN
def test_after_escape_no_input_action_runs() -> None:
    import desktop_guard as g
    import desktop_lock
    from desktop_ui import UIError
    g.halt()
    ran = []
    with pytest.raises(UIError, match="user_cancelled"), desktop_lock.input_lease():
        ran.append(1)
    assert not ran


def test_owner_activity_counts_only_real_input() -> None:
    import desktop_guard as g
    from desktop_ui import UIError
    with g._lock:
        g._state.update(owner_at=0.0, mouse=None)
    assert not g.owner_active()
    assert not g.on_key(g.WM_KEYDOWN, 0x41, g.LLKHF_INJECTED)      # Nova's own key
    assert not g.owner_active()
    assert not g.on_mouse(g.WM_MOUSEMOVE, 500, 500, 0)            # first position
    assert not g.on_mouse(g.WM_MOUSEMOVE, 502, 501, 0)            # sensor jitter
    assert not g.on_mouse(g.WM_MOUSEMOVE, 900, 900, g.LLMHF_INJECTED)   # injected move
    assert not g.owner_active()
    assert g.on_mouse(g.WM_MOUSEMOVE, 540, 520, 0)                # the owner moves it
    assert g.owner_active()
    with pytest.raises(UIError, match="user_active"):
        g.require_owner_idle()
    with g._lock:
        g._state["owner_at"] = 0.0
    g.on_key(g.WM_KEYDOWN, 0x41, 0)                               # the owner types "a"
    assert g.owner_active()
    with g._lock:
        g._state["owner_at"] -= g.USER_ACTIVE_SECONDS + 0.1        # a moment later
    assert not g.owner_active()
    assert g.on_mouse(0x0201, 540, 520, 0)                        # a real click counts


@WIN
def test_the_escape_hook_runs_and_ignores_novas_own_escape() -> None:
    import desktop_guard as g
    g.ESCAPE.arm()
    assert g.ESCAPE.armed()

    class KI(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t)]

    class INP(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("ki", KI), ("_pad", ctypes.c_byte * 16)]
    for flags in (0, 0x0002):                                    # key down, key up
        i = INP(type=1, ki=KI(g.VK_ESCAPE, 0, flags, 0, 0))
        ctypes.windll.user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(i))
    time.sleep(0.3)                     # the hook thread has seen it (injected → ignored)
    assert g.ESCAPE.armed() and not g.halted()
