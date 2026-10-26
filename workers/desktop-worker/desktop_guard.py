"""The owner stays in charge of the keyboard and mouse (plan §72 CU-3b).

- Owner active: if the owner pressed a key / clicked / moved the mouse in the last
  moment, Nova does not grab the foreground or send keys — the "jofresh" failure
  (2026-10-24), where the owner's typing landed in the window Nova was typing into.
  Seen through low-level hooks: only REAL input counts (injected events carry a flag)
  and tiny mouse jitter does not. Windows' own idle timer cannot be used — on this PC
  something resets it all the time with no key or mouse event at all (measured).
- Escape kill-switch: while Nova is acting, a low-level keyboard hook watches for a
  REAL Escape (not one Nova injected: LLKHF_INJECTED) and halts all further input for a
  while; the work loop sees "user_cancelled" and stops. Armed only while Nova acts.
  (Escape semantics after Dodaios and Stonic; written for Nova.)
- Foreground: Windows lets a background program bring a window forward only if it sent
  the last input — a zero-distance mouse move (harmless) satisfies that, and only when
  the owner is not active.
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from ctypes import wintypes
from typing import Any

from desktop_ui import UIError

USER_ACTIVE_SECONDS = 1.5      # owner input this recent → Nova waits
HALT_SECONDS = 15.0            # after a real Escape, all Nova input is refused this long
ARM_IDLE_SECONDS = 30.0        # the hook stays armed this long after Nova's last action

FIRST_LOOK_SECONDS = 0.3       # a fresh watch looks this long before trusting "idle"
JITTER_PX = 3                  # mouse moves this small are sensor noise, not the owner

_state: dict[str, Any] = {"owner_at": 0.0, "halted_at": 0.0, "mouse": None}
_lock = threading.Lock()


# ------------------------------------------------------------------ owner activity

def owner_input() -> None:
    with _lock:
        _state["owner_at"] = time.monotonic()


def owner_active(within: float = USER_ACTIVE_SECONDS) -> bool:
    """The owner (real keyboard/mouse, not Nova) gave input in the last `within` s.
    Only known while the watch is armed (Nova is acting)."""
    with _lock:
        at = _state["owner_at"]
    return bool(at) and time.monotonic() - at < within


def note_injected() -> None:
    """Nova's own input is flagged as injected by Windows — nothing to remember."""


def require_owner_idle(wait: float = 0.0) -> None:
    """`wait`: for an action the owner just asked for at their own keyboard (V1.2
    dictation — they released Ctrl+Alt+D a moment ago), wait up to this long for their
    hands to be still instead of refusing at once (owner 2026-10-26: refused twice)."""
    check_halt()
    if ESCAPE.arm():                         # a fresh watch: look a moment first
        time.sleep(FIRST_LOOK_SECONDS)
    until = time.monotonic() + wait
    while owner_active() and time.monotonic() < until:
        time.sleep(0.2)
        check_halt()
    if owner_active():
        raise UIError("user_active: the owner is using the keyboard/mouse — waiting, "
                      "not taking over")


# ------------------------------------------------------------------ Escape halt

def check_halt() -> None:
    with _lock:
        at = _state["halted_at"]
    if at and time.monotonic() - at < HALT_SECONDS:
        raise UIError("user_cancelled: the owner pressed Escape — Nova stopped")


def halt() -> None:
    with _lock:
        _state["halted_at"] = time.monotonic()


def clear_halt() -> None:
    with _lock:
        _state["halted_at"] = 0.0


def halted() -> bool:
    try:
        check_halt()
    except UIError:
        return True
    return False


VK_ESCAPE = 0x1B
LLKHF_INJECTED = 0x10
LLMHF_INJECTED = 0x01
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
WM_MOUSEMOVE = 0x0200


def on_key(message: int, vk: int, flags: int) -> bool:
    """The keyboard hook's decision (pure, testable). Any real key press = the owner
    is active; a real Escape also halts Nova (returns True)."""
    if message not in (WM_KEYDOWN, WM_SYSKEYDOWN) or flags & LLKHF_INJECTED:
        return False
    owner_input()
    if vk == VK_ESCAPE:
        halt()
        return True
    return False


def on_mouse(message: int, x: int, y: int, flags: int) -> bool:
    """The mouse hook's decision (pure): a real click/wheel, or a real move of more than
    a few pixels = the owner is active. Returns True when it counted."""
    if flags & LLMHF_INJECTED:
        return False
    with _lock:
        last = _state["mouse"]
        _state["mouse"] = (x, y)
    small = last is None or (abs(x - last[0]) <= JITTER_PX and abs(y - last[1]) <= JITTER_PX)
    if message == WM_MOUSEMOVE and small:
        return False
    owner_input()
    return True


class _EscapeHook:
    """WH_KEYBOARD_LL + WH_MOUSE_LL on their own thread with a message loop; they only
    watch (never block or change input) and stop ARM_IDLE_SECONDS after Nova's last
    action."""

    def __init__(self) -> None:
        self.thread: threading.Thread | None = None
        self.thread_id = 0
        self.last_use = 0.0
        self.mu = threading.Lock()
        self._proc: Any = None
        self.error = ""

    def arm(self) -> bool:
        """Keep the watch on; True if it was just started."""
        if sys.platform != "win32":
            return False
        with self.mu:
            self.last_use = time.monotonic()
            if self.thread is not None and self.thread.is_alive():
                return False
            ready = threading.Event()
            self.thread = threading.Thread(target=self._run, args=(ready,), daemon=True,
                                           name="nova-escape-hook")
            self.thread.start()
        ready.wait(2.0)
        return True

    def _run(self, ready: threading.Event) -> None:
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32

        class KBD(ctypes.Structure):
            _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                        ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_size_t)]

        class MSE(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG),
                        ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]
        hookproc = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM,
                                      wintypes.LPARAM)
        user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM,
                                          wintypes.LPARAM]
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, hookproc, wintypes.HINSTANCE,
                                             wintypes.DWORD]
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE   # 64-bit handle, not int
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                       wintypes.UINT, wintypes.UINT]

        def proc(code: int, wparam: int, lparam: int) -> int:
            if code == 0:
                k = ctypes.cast(lparam, ctypes.POINTER(KBD)).contents
                on_key(int(wparam), int(k.vkCode), int(k.flags))
            return int(user32.CallNextHookEx(None, code, wparam, lparam))

        def mproc(code: int, wparam: int, lparam: int) -> int:
            if code == 0:
                m = ctypes.cast(lparam, ctypes.POINTER(MSE)).contents
                on_mouse(int(wparam), int(m.x), int(m.y), int(m.flags))
            return int(user32.CallNextHookEx(None, code, wparam, lparam))

        self._proc = (hookproc(proc), hookproc(mproc))
        self.thread_id = kernel32.GetCurrentThreadId()
        module = kernel32.GetModuleHandleW(None)
        hook = user32.SetWindowsHookExW(13, self._proc[0], module, 0)
        mhook = user32.SetWindowsHookExW(14, self._proc[1], module, 0)
        if not hook or not mhook:
            self.error = f"SetWindowsHookEx failed ({ctypes.GetLastError()})"
            for h in (hook, mhook):
                if h:
                    user32.UnhookWindowsHookEx(h)
            ready.set()
            return
        self.error = ""
        ready.set()
        msg = wintypes.MSG()
        user32.SetTimer(None, 0, 1000, None)            # wake up to check idle time
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if time.monotonic() - self.last_use > ARM_IDLE_SECONDS:
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            user32.UnhookWindowsHookEx(hook)
            user32.UnhookWindowsHookEx(mhook)
            with _lock:
                _state["mouse"] = None

    def armed(self) -> bool:
        return self.thread is not None and self.thread.is_alive()


ESCAPE = _EscapeHook()


def acting(wait: float = 0.0) -> None:
    """Every input action starts here: refuse after a real Escape, and while the owner
    is using the keyboard/mouse (this also keeps the watch armed)."""
    require_owner_idle(wait)


# ------------------------------------------------------------------ foreground

def nudge_input() -> None:
    """A zero-distance mouse move: makes this process the last input sender (what
    Windows requires to set the foreground) without moving or clicking anything."""
    if sys.platform != "win32":
        return

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                    ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("mi", MOUSEINPUT),
                    ("_pad", ctypes.c_byte * 8)]
    inp = INPUT(type=0, mi=MOUSEINPUT(0, 0, 0, 0x0001, 0, 0))      # MOUSEEVENTF_MOVE
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
