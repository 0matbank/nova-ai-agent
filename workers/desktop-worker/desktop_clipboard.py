"""Clipboard of the user session (plan §14 clipboard skill)."""

from __future__ import annotations

import sys
import time
from typing import Any

MAX_CHARS = 100_000


def _open() -> Any:
    import win32clipboard as cb
    for _ in range(10):                  # another app may hold the clipboard briefly
        try:
            cb.OpenClipboard()
            return cb
        except Exception:
            time.sleep(0.05)
    raise RuntimeError("clipboard is busy")


def get_text() -> dict[str, Any]:
    if sys.platform != "win32":
        raise RuntimeError("clipboard is Windows-only here")
    import win32con
    cb = _open()
    try:
        if not cb.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            return {"has_text": False, "text": ""}
        text = str(cb.GetClipboardData(win32con.CF_UNICODETEXT))
    finally:
        cb.CloseClipboard()
    return {"has_text": True, "text": text[:MAX_CHARS], "truncated": len(text) > MAX_CHARS}


def set_text(text: str) -> dict[str, Any]:
    if sys.platform != "win32":
        raise RuntimeError("clipboard is Windows-only here")
    import win32con
    cb = _open()
    try:
        cb.EmptyClipboard()
        cb.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        cb.CloseClipboard()
    return {"verified": get_text().get("text") == text[:MAX_CHARS]}
