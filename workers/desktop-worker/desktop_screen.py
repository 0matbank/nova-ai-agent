"""Lock detection + screenshots inside the user session (plan §5, §15, §57)."""

from __future__ import annotations

import base64
import io
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class DesktopLocked(Exception):
    pass


def is_locked() -> bool:
    """True when the interactive desktop is not the user's (lock screen / UAC).
    When locked, the input desktop is Winlogon and cannot be opened by the user."""
    if sys.platform != "win32":
        return False
    import win32con
    import win32service

    try:
        desk = win32service.OpenInputDesktop(0, False, win32con.DESKTOP_READOBJECTS)
    except Exception:
        return True
    try:
        name = win32service.GetUserObjectInformation(desk, win32con.UOI_NAME)
    finally:
        desk.CloseDesktop()
    return str(name).lower() != "default"


def require_unlocked() -> None:
    if is_locked():
        raise DesktopLocked("desktop is locked")


def capture(out_dir: Path, monitor: int = 0, preview_width: int = 1280) -> dict[str, Any]:
    """monitor=0 → all monitors as one image; 1..n → a single monitor.
    Saves a full-resolution PNG locally; returns a small JPEG preview for chat."""
    require_unlocked()
    import mss
    from PIL import Image

    out_dir.mkdir(parents=True, exist_ok=True)
    factory = getattr(mss, "MSS", None) or mss.mss
    with factory() as sct:
        if monitor < 0 or monitor >= len(sct.monitors):
            raise ValueError(f"monitor must be 0..{len(sct.monitors) - 1}")
        shot = sct.grab(sct.monitors[monitor])
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        monitors = len(sct.monitors) - 1
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    path = out_dir / f"screen-{stamp}.png"
    img.save(path, "PNG", optimize=True)
    preview = img.copy()
    if preview.width > preview_width:
        preview.thumbnail((preview_width, preview_width * preview.height // preview.width))
    buf = io.BytesIO()
    preview.save(buf, "JPEG", quality=80)
    # A near-uniform image usually means a black/blank capture (e.g. secure desktop).
    extrema = img.convert("L").getextrema()
    blank = extrema[1] - extrema[0] < 8
    return {"path": str(path), "width": img.width, "height": img.height,
            "monitors": monitors, "blank": blank,
            "preview_jpeg_b64": base64.b64encode(buf.getvalue()).decode()}
