"""V1.2 floating orb (plan §65 V1.2-5): a small always-on-top light in the corner of the
PC screen — red while Nova listens (push-to-talk held), amber while it thinks, blue while
it speaks; invisible when idle.

It must never get in the owner's way (window tricks as in Atticus, MIT — written for
Nova): it never takes the keyboard focus (WS_EX_NOACTIVATE), clicks go through it
(WS_EX_TRANSPARENT), it has no taskbar button (WS_EX_TOOLWINDOW), and it is left out of
every screenshot (WDA_EXCLUDEFROMCAPTURE) — also out of Nova's own screen reading.
Tk runs on its own thread; `Orb.set` is safe from any thread. Standard library only.
"""

from __future__ import annotations

import contextlib
import queue
import sys
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from core.log import get_logger

_log = get_logger("core")

IDLE, LISTENING, THINKING, SPEAKING = "idle", "listening", "thinking", "speaking"
COLORS = {LISTENING: "#E5484D", THINKING: "#F5A524", SPEAKING: "#3E8BFF"}
# thinking with nothing said afterwards (a silent result, an answer only in Telegram)
THINKING_SECONDS = 90.0
SIZE = 44
MARGIN = 24

GWL_EXSTYLE = -20
WS_EX_TOPMOST, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW = 0x8, 0x20, 0x80
WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x80000, 0x8000000
WDA_EXCLUDEFROMCAPTURE = 0x11
SW_SHOWNOACTIVATE = 4
LSFW_LOCK, LSFW_UNLOCK = 1, 2
LWA_COLORKEY, LWA_ALPHA = 0x1, 0x2
KEY_COLORREF = 0x030201            # #010203 as Windows writes a colour (BGR)


class View(Protocol):
    def show(self, state: str) -> None: ...
    def close(self) -> None: ...


class Orb:
    """What the light shows. Speaking wins over thinking; a new key press wins over all."""

    def __init__(self, view: View | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.view = view
        self.clock = clock
        self.state = IDLE
        self.speaking = 0                    # answers playing right now
        self.thinking_since = 0.0
        self._lock = threading.Lock()

    def _show(self, state: str) -> None:
        if state == self.state:
            return
        self.state = state
        if self.view is not None:
            try:
                self.view.show(state)
            except Exception:  # noqa: BLE001 - a light must never break voice
                _log.exception("orb view failed", extra={"action": "voice.orb"})

    def listening(self) -> None:
        with self._lock:
            self._show(LISTENING)

    def thinking(self) -> None:
        with self._lock:
            self.thinking_since = self.clock()
            if self.speaking == 0:
                self._show(THINKING)

    def idle(self) -> None:
        """The key press was too short, or the turn ended without anything to say."""
        with self._lock:
            if self.speaking == 0 and self.state != LISTENING:
                self._show(IDLE)

    def speech_started(self) -> None:
        with self._lock:
            self.speaking += 1
            if self.state != LISTENING:
                self._show(SPEAKING)

    def speech_ended(self) -> None:
        with self._lock:
            self.speaking = max(0, self.speaking - 1)
            if self.speaking == 0 and self.state == SPEAKING:
                self._show(IDLE)

    def interrupted(self) -> None:
        with self._lock:
            self.speaking = 0

    def tick(self) -> None:
        """Called now and then: thinking that never led to speech fades out."""
        with self._lock:
            if (self.state == THINKING and self.speaking == 0
                    and self.clock() - self.thinking_since > THINKING_SECONDS):
                self._show(IDLE)


class TkView:
    """The real window (Windows), on its own thread."""

    def __init__(self, tick: Callable[[], None] | None = None) -> None:
        self._q: queue.Queue[str | None] = queue.Queue()
        self._tick = tick
        self.hwnd = 0
        self.error = ""
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="nova-orb", daemon=True)
        self._thread.start()
        self._ready.wait(5)

    def show(self, state: str) -> None:
        self._q.put(state)

    def close(self) -> None:
        self._q.put(None)
        self._thread.join(3)

    def _run(self) -> None:
        try:
            self._ui()
        except Exception as e:  # noqa: BLE001 - no display / no Tk: voice works without it
            self.error = f"{type(e).__name__}: {e}"
            self._ready.set()
        # Tcl must be torn down on the thread that made it (else "Tcl_AsyncDelete:
        # async handler deleted by the wrong thread" when another thread drops it)
        import gc
        gc.collect()

    def _ui(self) -> None:
        import ctypes
        user32 = ctypes.windll.user32
        # measured 2026-10-26: Tk's first window took the focus at once — a process
        # started from the window in front may take the focus once, and Tk used it.
        # Windows is told not to hand the focus over while the window is made.
        user32.LockSetForegroundWindow(LSFW_LOCK)
        try:
            root, canvas, dot = self._window()
        finally:
            user32.LockSetForegroundWindow(LSFW_UNLOCK)
        self._ready.set()
        state: dict[str, Any] = {"now": IDLE, "phase": 0}

        def pump() -> None:
            try:
                while True:
                    item = self._q.get_nowait()
                    if item is None:
                        root.destroy()
                        return
                    state["now"] = item
            except queue.Empty:
                pass
            now = state["now"]
            if now == IDLE:
                _fade(self.hwnd, 0.0)
            else:
                # a soft pulse: the light breathes while Nova works
                state["phase"] = (state["phase"] + 1) % 20
                canvas.itemconfigure(dot, fill=COLORS.get(now, COLORS[THINKING]))
                _fade(self.hwnd, 0.55 + 0.4 * abs(10 - state["phase"]) / 10)
            if self._tick is not None:
                with contextlib.suppress(Exception):
                    self._tick()
            root.after(80 if now != IDLE else 250, pump)

        root.after(0, pump)
        root.mainloop()

    def _window(self) -> tuple[Any, Any, Any]:
        import tkinter as tk
        root = tk.Tk()
        key = "#010203"                      # the see-through colour around the circle
        # measured 2026-10-26: a Tk window that is shown — or given Tk's own alpha or
        # see-through colour — takes the keyboard focus. So it stays hidden until it can
        # never be activated, is shown without activation, and its see-through colour
        # and fading are set by Windows directly, never through Tk.
        root.withdraw()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        _left, _top, right, bottom = _work_area(root)
        root.geometry(f"{SIZE}x{SIZE}+{right - SIZE - MARGIN}+{bottom - SIZE - MARGIN}")
        canvas = tk.Canvas(root, width=SIZE, height=SIZE, bg=key, highlightthickness=0)
        canvas.pack()
        dot = canvas.create_oval(4, 4, SIZE - 4, SIZE - 4, fill=key, outline="")
        root.update_idletasks()
        self.hwnd = _harden(root)
        if not self.hwnd:
            raise RuntimeError("no window handle (not Windows)")
        _fade(self.hwnd, 0.0)
        import ctypes
        ctypes.windll.user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
        root.update()
        return root, canvas, dot


def _work_area(root: Any) -> tuple[int, int, int, int]:
    """The screen minus the taskbar (primary monitor)."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        rect = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rect), 0):
            return rect.left, rect.top, rect.right, rect.bottom
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def _fade(hwnd: int, alpha: float) -> None:
    """See-through around the circle (colour key) and the light's strength — by Windows
    itself (Tk's own "-alpha" / "-transparentcolor" bring the window to the front)."""
    import ctypes
    ctypes.windll.user32.SetLayeredWindowAttributes(
        hwnd, KEY_COLORREF, int(max(0.0, min(1.0, alpha)) * 255), LWA_COLORKEY | LWA_ALPHA)


def _harden(root: Any) -> int:
    """No focus, click-through, no taskbar button, never in a screenshot."""
    if sys.platform != "win32":
        return 0
    import ctypes
    user32 = ctypes.windll.user32
    hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()
    user32.GetWindowLongW.restype = ctypes.c_long
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT
                          | WS_EX_TOOLWINDOW | WS_EX_LAYERED | WS_EX_TOPMOST)
    if not user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE):
        _log.warning("orb: could not keep it out of screenshots",
                     extra={"action": "voice.orb", "status": "error"})
    return int(hwnd)
