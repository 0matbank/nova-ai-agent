"""Observer for the general computer-use engine (plan §71, CU-1): what ONE window shows,
for any app — no app, button or layout names anywhere in here.

  1. One cached UI Automation query for all the window's controls (the pattern of
     Microsoft UFO's UIABackendStrategy.find_control_elements_in_descendants, MIT
     License, Copyright (c) Microsoft Corporation): FindAllBuildCache with the
     properties prefetched, instead of walking the tree control by control.
  2. A screenshot of just that window, and a copy with every control boxed and
     numbered (set-of-marks) for a vision model.
  3. "sparse": the tree tells almost nothing (web-style / canvas / custom-drawn apps) —
     then the caller adds vision grounding (CU-2).

Element numbers are valid for this observation only. Screen text is untrusted data.
"""

from __future__ import annotations

import base64
import io
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from desktop_screen import require_unlocked
from desktop_ui import UIError, _activate, _describe_window, _uia

# Control types worth showing (UFO's default CONTROL_LIST plus a few common ones)
CONTROL_TYPES = ("Button", "Edit", "TabItem", "Document", "ListItem", "MenuItem", "ScrollBar",
                 "TreeItem", "Hyperlink", "ComboBox", "RadioButton", "Spinner", "CheckBox",
                 "Group", "Text", "SplitButton", "Slider", "DataItem", "MenuBar", "Menu",
                 "ToolBar", "Image", "Header", "HeaderItem", "Tab", "List", "Tree",
                 "TitleBar")
# things a person can act on (Text / Group / Image only describe)
ACTIONABLE = {"Button", "Edit", "TabItem", "ListItem", "MenuItem", "TreeItem", "Hyperlink",
              "ComboBox", "RadioButton", "Spinner", "CheckBox", "SplitButton", "Slider",
              "DataItem", "HeaderItem", "Document"}
FRAME_BUTTONS = {"minimize", "maximize", "restore", "close", "system"}
FRAME_STRIP = 48           # px from the window's top edge
MAX_ELEMENTS = 400
RETRY_SECONDS = 0.6
MIN_ACTIONABLE = 3         # fewer named actionable controls → the tree is "sparse"
PREVIEW_WIDTH = 1280
_COLORS = {"Button": (255, 196, 0), "Edit": (0, 170, 90), "MenuItem": (0, 120, 255),
           "Hyperlink": (180, 0, 200), "CheckBox": (255, 90, 0), "RadioButton": (255, 90, 0),
           "ComboBox": (0, 160, 200), "Text": (140, 140, 140)}


def _rect(r: Any) -> tuple[int, int, int, int]:
    return int(r.left), int(r.top), int(r.right), int(r.bottom)


def _type_names(auto: Any) -> dict[int, str]:
    return {v: k.removesuffix("Control") for k, v in vars(auto.ControlType).items()
            if isinstance(v, int) and k.endswith("Control")}


def _collect(auto: Any, window: Any) -> list[dict[str, Any]]:
    """All visible controls of `window` in one cached query (UFO's approach)."""
    from uiautomation.uiautomation import _AutomationClient
    client = _AutomationClient.instance()
    iuia, core = client.IUIAutomation, client.UIAutomationCore
    p = auto.PropertyId
    names = _type_names(auto)
    wanted = [v for v, n in names.items() if n in CONTROL_TYPES]
    cache = iuia.CreateCacheRequest()
    for prop in (p.ControlTypeProperty, p.NameProperty, p.BoundingRectangleProperty,
                 p.AutomationIdProperty, p.IsEnabledProperty, p.HasKeyboardFocusProperty,
                 p.ClassNameProperty, p.ValueValueProperty):
        cache.AddProperty(prop)
    cond = iuia.CreateAndConditionFromArray([
        iuia.CreatePropertyCondition(p.IsOffscreenProperty, False),
        iuia.CreatePropertyCondition(p.IsControlElementProperty, True),
        iuia.CreateOrConditionFromArray([iuia.CreatePropertyCondition(p.ControlTypeProperty, t)
                                         for t in wanted]),
    ])
    found = window.Element.FindAllBuildCache(core.TreeScope_Descendants, cond, cache)
    out = []
    for i in range(min(found.Length, MAX_ELEMENTS)):
        e = found.GetElement(i)
        try:
            out.append({"type": names.get(e.CachedControlType, str(e.CachedControlType)),
                        "name": (e.CachedName or "").strip()[:200],
                        "automation_id": (e.CachedAutomationId or "")[:80],
                        "class": (e.CachedClassName or "")[:60],
                        "enabled": bool(e.CachedIsEnabled),
                        "focused": bool(e.CachedHasKeyboardFocus),
                        "value": _cached_value(e, p.ValueValueProperty),
                        "rect": _rect(e.CachedBoundingRectangle)})
        except Exception:  # noqa: BLE001 - one vanished control must not break the rest
            continue
    return out


def _cached_value(e: Any, prop: int) -> str:
    try:
        v = e.GetCachedPropertyValue(prop)
    except Exception:  # noqa: BLE001 - no Value pattern
        return ""
    return str(v)[:200] if isinstance(v, str) else ""


def _within(r: tuple[int, int, int, int], box: tuple[int, int, int, int], slack: int = 2
            ) -> bool:
    return (r[0] >= box[0] - slack and r[1] >= box[1] - slack and r[2] <= box[2] + slack
            and r[3] <= box[3] + slack)


def _in_strip(r: tuple[int, int, int, int], bar: tuple[int, int, int, int],
              win: tuple[int, int, int, int]) -> bool:
    """The control's centre lies in the title-bar strip (its caption buttons stick out a
    few pixels past the bar's own box)."""
    cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
    return bar[1] - 4 <= cy <= bar[3] + 4 and win[0] <= cx <= win[2]


def number(elements: list[dict[str, Any]], win: tuple[int, int, int, int]
           ) -> list[dict[str, Any]]:
    """Keep controls with a real box inside the window, in reading order, numbered 1…n."""
    wl, wt, wr, wb = win
    keep = []
    for el in elements:
        left, top, right, bottom = el["rect"]
        if right - left < 2 or bottom - top < 2:
            continue
        if right <= wl or left >= wr or bottom <= wt or top >= wb:
            continue                                      # outside the window
        keep.append(el)
    # the window's own title bar (minimize / maximize / close / system menu) is the
    # frame, not the app's content
    bars = [e["rect"] for e in keep if e["type"] == "TitleBar"]
    keep = [e for e in keep if e["type"] != "TitleBar"]
    # a control's own label is not a second thing to act on ("Save" text in "Save" button)
    owners = [e for e in keep if e["type"] in ACTIONABLE]
    keep = [e for e in keep if not (e["type"] == "Text" and any(
        o is not e and _within(e["rect"], o["rect"])
        and (not e["name"] or e["name"] in (o["name"] or o.get("value") or ""))
        for o in owners))]
    keep.sort(key=lambda e: (e["rect"][1] // 8, e["rect"][0]))
    for n, el in enumerate(keep, 1):
        el["id"] = n
        el["actionable"] = el["type"] in ACTIONABLE
        el["chrome"] = any(_in_strip(el["rect"], b, win) for b in bars) or (
            # apps that draw their own title bar (Chromium/Electron) expose no TitleBar:
            # Windows' standard frame buttons at the very top of the window
            el["name"].lower() in FRAME_BUTTONS and el["rect"][1] - wt < FRAME_STRIP)
    return keep


def is_sparse(elements: list[dict[str, Any]]) -> bool:
    named = [e for e in elements if e["actionable"] and not e["chrome"]
             and (e["name"] or e.get("value"))]
    return len(named) < MIN_ACTIONABLE


def _grab(rect: tuple[int, int, int, int]) -> Any:
    import mss
    from PIL import Image
    left, top, right, bottom = rect
    factory = getattr(mss, "MSS", None) or mss.mss
    with factory() as sct:
        v = sct.monitors[0]                               # the whole virtual screen
        left, top = max(left, v["left"]), max(top, v["top"])
        right = min(right, v["left"] + v["width"])
        bottom = min(bottom, v["top"] + v["height"])
        if right <= left or bottom <= top:
            raise UIError("the window is off screen")
        shot = sct.grab({"left": left, "top": top, "width": right - left,
                         "height": bottom - top})
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX"), (left, top)


def marks(img: Any, origin: tuple[int, int], elements: list[dict[str, Any]]) -> Any:
    """Set-of-marks: every control boxed with its number (for the vision model)."""
    from PIL import ImageDraw
    out = img.copy()
    d = ImageDraw.Draw(out)
    ox, oy = origin
    for el in elements:
        left, top, right, bottom = el["rect"]
        color = _COLORS.get(el["type"], (255, 0, 80))
        box = (left - ox, top - oy, right - ox - 1, bottom - oy - 1)
        d.rectangle(box, outline=color, width=2)
        tag = str(el["id"])
        tx, ty = box[0], max(0, box[1] - 12)
        d.rectangle((tx, ty, tx + 7 * len(tag) + 4, ty + 12), fill=color)
        d.text((tx + 2, ty), tag, fill=(0, 0, 0))
    return out


def _jpeg_b64(img: Any) -> str:
    small = img.copy()
    if small.width > PREVIEW_WIDTH:
        small.thumbnail((PREVIEW_WIDTH, PREVIEW_WIDTH * small.height // small.width))
    buf = io.BytesIO()
    small.save(buf, "JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode()


def find_hwnd(spec: str) -> int:
    """A visible top-level window by title — exact first, then a unique part. Native
    EnumWindows: much faster than walking UI Automation's root."""
    import win32gui
    wins: list[tuple[int, str]] = []

    def cb(hwnd: int, _: Any) -> bool:
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title:
                wins.append((hwnd, title))
        return True

    win32gui.EnumWindows(cb, None)
    s = spec.strip().lower()
    exact = [h for h, t in wins if t.lower() == s]
    hits = exact or [h for h, t in wins if s in t.lower()]
    if not hits:
        raise UIError(f"no window matches {spec!r}")
    if len(hits) > 1 and not exact:
        raise UIError(f"{len(hits)} windows match {spec!r} — be more specific")
    return hits[0]


def _target(auto: Any, window: str | None, hwnd: int | None) -> Any:
    if hwnd:
        w = auto.ControlFromHandle(hwnd)
        if w is None:
            raise UIError(f"no window with handle {hwnd}")
        return w
    if window:
        return auto.ControlFromHandle(find_hwnd(window))
    fg = auto.GetForegroundControl()
    if fg is None:
        raise UIError("no window is in front")
    return fg.GetTopLevelControl()


def _is_front(auto: Any, w: Any) -> bool:
    fg = auto.GetForegroundControl()
    top = fg.GetTopLevelControl() if fg else None
    return top is not None and top.NativeWindowHandle == w.NativeWindowHandle


def observe(out_dir: Path, window: str | None = None, hwnd: int | None = None,
            image: bool = True, focus: bool = False) -> dict[str, Any]:
    """`focus`: bring the window to the front first (the work loop does; a read-only
    look does not). Browser/Electron windows that are fully covered show no content."""
    require_unlocked()
    t0 = time.perf_counter()
    with _uia() as auto:
        w = _target(auto, window, hwnd)
        if focus and not _is_front(auto, w):
            _activate(w)
        front = _is_front(auto, w)
        info = _describe_window(w)
        win = _rect(w.BoundingRectangle)
        raw = _collect(auto, w)
        elements = number(raw, win)
        if is_sparse(elements):
            # apps (browsers especially) fill their accessibility tree a moment after
            # drawing: one more look before calling the window unreadable
            time.sleep(RETRY_SECONDS)
            again = number(_collect(auto, w), win)
            if len(again) > len(elements):
                elements = again
    t1 = time.perf_counter()
    out: dict[str, Any] = {"window": {**info, "rect": win, "foreground": front},
                           "elements": elements, "sparse": is_sparse(elements),
                           "timing_ms": {"tree": round((t1 - t0) * 1000)}}
    if image:
        img, origin = _grab(win)
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        plain, marked = out_dir / f"obs-{stamp}.png", out_dir / f"obs-{stamp}-marks.png"
        img.save(plain, "PNG")
        m = marks(img, origin, elements)
        m.save(marked, "PNG")
        extrema = img.convert("L").getextrema()
        out.update({"image": str(plain), "marked": str(marked), "origin": origin,
                    "size": img.size, "blank": extrema[1] - extrema[0] < 8,
                    "marked_jpeg_b64": _jpeg_b64(m)})
        out["timing_ms"]["image"] = round((time.perf_counter() - t1) * 1000)
    out["timing_ms"]["total"] = round((time.perf_counter() - t0) * 1000)
    return out


if sys.platform == "win32":                     # boxes and pixels on the same scale
    import contextlib
    import ctypes
    with contextlib.suppress(Exception):         # already set by the process: fine
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # per-monitor DPI aware
