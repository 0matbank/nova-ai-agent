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

Element numbers are display labels valid for this observation only. Each observation
gets an id and is kept briefly (CU-3): an action names (observation id, element number)
and the worker proves it is still the SAME element — same window, same process, same
UI Automation runtime id, same place — or refuses (never another button with the same
label). Screen text is untrusted data.
"""

from __future__ import annotations

import base64
import io
import sys
import threading
import time
import uuid
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from desktop_screen import require_unlocked
from desktop_ui import UIError, _activate, _describe_window, _uia, front_window

# Control types worth showing (UFO's default CONTROL_LIST plus a few common ones)
CONTROL_TYPES = ("Button", "Edit", "TabItem", "Document", "ListItem", "MenuItem", "ScrollBar",
                 "TreeItem", "Hyperlink", "ComboBox", "RadioButton", "Spinner", "CheckBox",
                 "Group", "Text", "SplitButton", "Slider", "DataItem", "MenuBar", "Menu",
                 "ToolBar", "Image", "Header", "HeaderItem", "Tab", "List", "Tree",
                 "TitleBar", "Pane")
# things a person can act on (Text / Group / Image only describe)
ACTIONABLE = {"Button", "Edit", "TabItem", "ListItem", "MenuItem", "TreeItem", "Hyperlink",
              "ComboBox", "RadioButton", "Spinner", "CheckBox", "SplitButton", "Slider",
              "DataItem", "HeaderItem", "Document", "Pane"}
FRAME_BUTTONS = {"minimize", "maximize", "restore", "close", "system"}
FRAME_STRIP = 48           # px from the window's top edge
KEEP_OBSERVATIONS = 16
OBSERVATION_TTL = 300.0    # s; the element check at action time is the real guard
MAX_ELEMENTS = 400
RETRY_SECONDS = 0.6
# a focusable pane at least this big is an editing/drawing surface (Scintilla editors,
# canvases, terminals) — other panes are only layout and are left out (CU-9 2026-10-24)
SURFACE_MIN = (120, 60)
SURFACE_TEXT_MAX = 20000
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


def _collect(auto: Any, window: Any, keep: bool = False) -> list[dict[str, Any]]:
    """All visible controls of `window` in one cached query (UFO's approach).
    keep=True adds the live COM element under "_el" (same thread only)."""
    from uiautomation.uiautomation import _AutomationClient
    client = _AutomationClient.instance()
    iuia, core = client.IUIAutomation, client.UIAutomationCore
    p = auto.PropertyId
    names = _type_names(auto)
    wanted = [v for v, n in names.items() if n in CONTROL_TYPES]
    cache = iuia.CreateCacheRequest()
    for prop in (p.ControlTypeProperty, p.NameProperty, p.BoundingRectangleProperty,
                 p.AutomationIdProperty, p.IsEnabledProperty, p.HasKeyboardFocusProperty,
                 p.ClassNameProperty, p.ValueValueProperty, p.RuntimeIdProperty,
                 p.ToggleToggleStateProperty, p.SelectionItemIsSelectedProperty,
                 p.IsPasswordProperty, p.IsKeyboardFocusableProperty,
                 *(getattr(p, f"Is{name}PatternAvailableProperty") for name, _ in PATTERN_OPS)):
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
            item = {"type": names.get(e.CachedControlType, str(e.CachedControlType)),
                    "name": (e.CachedName or "").strip()[:200],
                    "automation_id": (e.CachedAutomationId or "")[:80],
                    "class": (e.CachedClassName or "")[:60],
                    "enabled": bool(e.CachedIsEnabled),
                    "focused": bool(e.CachedHasKeyboardFocus),
                    "value": _cached_value(e, p.ValueValueProperty),
                    "checked": _cached_toggle(e, p.ToggleToggleStateProperty),
                    "selected": _cached_bool(e, p.SelectionItemIsSelectedProperty),
                    # the app itself marks secret fields (any toolkit): RED to type
                    "password": _cached_bool(e, p.IsPasswordProperty) is True,
                    "focusable": _cached_bool(e, p.IsKeyboardFocusableProperty) is True,
                    "ops": _ops(e, p),
                    "runtime_id": _runtime_id(e, p.RuntimeIdProperty),
                    "rect": _rect(e.CachedBoundingRectangle),
                    **({"_el": e} if keep else {})}
        except Exception:  # noqa: BLE001 - one vanished control must not break the rest
            continue
        if item["type"] == "Pane":
            left, top, right, bottom = item["rect"]
            if not (item["focusable"] and right - left >= SURFACE_MIN[0]
                    and bottom - top >= SURFACE_MIN[1]):
                continue                              # layout, not something to use
            item["ops"] = ["type", "focus"]           # keyboard at its cursor
            full = e.CachedName or ""
            item["name"] = full[:SURFACE_TEXT_MAX]    # an editor's whole text (proofs)
            item["tail"] = full[-200:]                # how the text ends
            item["length"] = len(full)
            if out and out[-1]["type"] == "Pane" and out[-1]["rect"] == item["rect"]:
                out.pop()                             # a frame around it: keep the inner one
        out.append(item)
    return out


def _runtime_id(e: Any, prop: int) -> tuple[int, ...]:
    try:
        v = e.GetCachedPropertyValue(prop)
    except Exception:  # noqa: BLE001 - no runtime id
        return ()
    try:
        return tuple(int(x) for x in v)
    except TypeError:
        return ()


# what Windows says a control can really do → the operations Nova may use on it
PATTERN_OPS = (("Invoke", "click"), ("Toggle", "toggle"), ("SelectionItem", "select"),
               ("ExpandCollapse", "expand"), ("Value", "set_text"),
               ("ScrollItem", "scroll_into_view"))


def _cached_bool(e: Any, prop: int) -> bool | None:
    try:
        v = e.GetCachedPropertyValue(prop)
    except Exception:  # noqa: BLE001
        return None
    return bool(v) if isinstance(v, (bool, int)) else None


def _ops(e: Any, p: Any) -> list[str]:
    """The operations this control supports (from UIA pattern availability)."""
    return [op for name, op in PATTERN_OPS
            if _cached_bool(e, getattr(p, f"Is{name}PatternAvailableProperty"))]


def _cached_toggle(e: Any, prop: int) -> bool | None:
    """Check box / toggle state: True on, False off, None when it has no such state."""
    try:
        v = e.GetCachedPropertyValue(prop)
    except Exception:  # noqa: BLE001 - no Toggle pattern
        return None
    return {0: False, 1: True}.get(v) if isinstance(v, int) else None


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
        el["actionable"] = el["type"] in ACTIONABLE and (
            bool(el.get("ops")) or "ops" not in el)
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


# ------------------------------------------------------------- observation store

_STORE: OrderedDict[str, dict[str, Any]] = OrderedDict()
_STORE_LOCK = threading.Lock()


def process_signature(pid: int) -> float:
    """The process start time: a reused PID is a different program."""
    import psutil
    try:
        return float(psutil.Process(pid).create_time())
    except psutil.Error:
        return 0.0


def _remember(window: dict[str, Any], elements: list[dict[str, Any]]) -> str:
    oid = uuid.uuid4().hex[:12]
    with _STORE_LOCK:
        _STORE[oid] = {"hwnd": window["hwnd"], "pid": window["pid"],
                       "created": process_signature(window["pid"]), "at": time.monotonic(),
                       "elements": {e["id"]: {
                           **{k: e[k] for k in ("rect", "name", "type", "automation_id",
                                                "value")},
                           "hwnd": e.get("hwnd", window["hwnd"]),
                           "runtime_id": e.get("runtime_id", ())} for e in elements}}
        while len(_STORE) > KEEP_OBSERVATIONS:
            _STORE.popitem(last=False)
    return oid


def recall(observation_id: str) -> dict[str, Any]:
    with _STORE_LOCK:
        obs = _STORE.get(observation_id)
    if obs is None:
        raise UIError("stale_target: that observation is unknown or too old — observe again")
    if time.monotonic() - obs["at"] > OBSERVATION_TTL:
        raise UIError("stale_target: that observation is too old — observe again")
    return obs


def _target(auto: Any, window: str | None, hwnd: int | None) -> Any:
    if hwnd:
        w = auto.ControlFromHandle(hwnd)
        if w is None:
            raise UIError(f"no window with handle {hwnd}")
        return w
    if window:
        return auto.ControlFromHandle(find_hwnd(window))
    top = front_window(auto)
    if top is None:
        raise UIError("no window is in front")
    return top


WS_POPUP = 0x80000000


def _popups(auto: Any, main: Any) -> list[Any]:
    """Visible pop-up windows of the same program (a drop-down list, a context menu):
    they are separate top-level windows in Windows, but part of what the user sees."""
    if sys.platform != "win32":
        return []
    import win32con
    import win32gui
    import win32process
    pid, me = main.ProcessId, int(main.NativeWindowHandle)
    found: list[int] = []

    def cb(hwnd: int, _: Any) -> bool:
        if hwnd != me and win32gui.IsWindowVisible(hwnd) and \
                win32process.GetWindowThreadProcessId(hwnd)[1] == pid and \
                win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE) & WS_POPUP:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            if right - left > 4 and bottom - top > 4:
                found.append(hwnd)
        return True

    win32gui.EnumWindows(cb, None)
    out = []
    for h in found[:4]:
        try:
            out.append(auto.ControlFromHandle(h))
        except Exception:  # noqa: BLE001 - it closed meanwhile
            continue
    return out


def _is_front(auto: Any, w: Any) -> bool:
    top = front_window(auto)
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
        for el in elements:
            el["hwnd"] = info["hwnd"]
        for pw in _popups(auto, w):
            extra = number(_collect(auto, pw), _rect(pw.BoundingRectangle))
            for el in extra:
                el.update(hwnd=int(pw.NativeWindowHandle), popup=True)
            elements += extra
        for n, el in enumerate(elements, 1):
            el["id"] = n
    t1 = time.perf_counter()
    oid = _remember(info, elements)
    for el in elements:
        el.pop("runtime_id", None)                 # internal identity, not for the model
    out: dict[str, Any] = {"observation_id": oid,
                           "window": {**info, "rect": win, "foreground": front},
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
