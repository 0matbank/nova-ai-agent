"""Act on ONE element of ONE observation (plan §72 CU-3) — for any app.

The caller names (observation_id, element number) from desktop_observe. Before any input
the worker proves the target is still the same: the window still exists and belongs to
the same process (PID + start time — a reused PID is another program), and the element
is found again by its UI Automation runtime id (or, if the app gives none, by exactly
one control with the same type, name, id and box). Moved, renamed, replaced, disabled →
"stale_target"/"disabled", never a silent fall-back to another button with the same
label (ego-lite's rule). UIA patterns only here (no mouse); every result says what was
delivered and whether the app visibly changed (Hermes' ActionResult split:
transport ok ≠ effect)."""

from __future__ import annotations

import re
import time
from typing import Any

from desktop_lock import input_lease
from desktop_observe import _collect, process_signature, recall
from desktop_screen import require_unlocked
from desktop_ui import (
    UIError,
    _activate,
    _escape_sendkeys,
    _focus_inside,
    _proc_name,
    _uia,
    front_window,
    is_shell_window,
    win32_class,
)

MOVE_TOLERANCE = 4         # px a control may shift (re-layout) and still be "the same"
OPS = ("click", "set_text", "focus", "toggle", "select", "expand", "collapse",
       "scroll_into_view", "type")
SETTLE_SECONDS = 0.35


def _same_box(a: tuple[int, ...], b: tuple[int, ...], tol: int = MOVE_TOLERANCE) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b, strict=True))


def find_again(want: dict[str, Any], now: list[dict[str, Any]]) -> dict[str, Any]:
    """The element of an observation in a fresh collection, or UIError(stale_target)."""
    rid = tuple(want.get("runtime_id") or ())
    if rid:
        hits = [c for c in now if tuple(c.get("runtime_id") or ()) == rid]
    else:                                 # no runtime id: identity by everything else
        hits = [c for c in now if c["type"] == want["type"] and c["name"] == want["name"]
                and c["automation_id"] == want["automation_id"]
                and _same_box(c["rect"], want["rect"], 2)]
    if len(hits) != 1:
        raise UIError("stale_target: the element is gone or was replaced — observe again")
    c = hits[0]
    if c["type"] != want["type"] or c["name"] != want["name"]:
        raise UIError(f"stale_target: the element changed ({want['name']!r} → "
                      f"{c['name']!r}) — observe again")
    if not _same_box(c["rect"], want["rect"]):
        raise UIError("stale_target: the element moved — observe again")
    return c


def content_signature(elements: list[dict[str, Any]]) -> int:
    """What the window shows, cheaply: control types, names, values and boxes."""
    return hash(tuple((e["type"], e["name"], e.get("value", ""), e["rect"])
                      for e in elements))


def _state(el: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, getter, attr in (("value", "GetValuePattern", "Value"),
                              ("toggle", "GetTogglePattern", "ToggleState"),
                              ("selected", "GetSelectionItemPattern", "IsSelected"),
                              ("expanded", "GetExpandCollapsePattern",
                               "ExpandCollapseState")):
        try:
            out[key] = getattr(getattr(el, getter)(), attr)
        except Exception:  # noqa: BLE001 - pattern not supported
            continue
    return out


def _window_state(auto: Any, w: Any) -> dict[str, Any]:
    try:
        kids = [c.Name for c in w.GetChildren() if c.ControlTypeName == "WindowControl"]
    except Exception:  # noqa: BLE001
        kids = []
    top = front_window(auto)
    return {"dialogs": sorted(kids), "exists": bool(w.Exists(0, 0)),
            "foreground": top.Name if top is not None else None}


AT_RX = re.compile(r"^(end|new_line|cursor|line_(end|start):(\d{1,6}))$")


def place_keys(at: str, tail: str = "") -> str:
    """The key sequence that puts the cursor where `at` says (any editor: standard
    Windows editing keys only)."""
    m = AT_RX.match(at)
    if not m:
        raise UIError(f"invalid place {at!r} — use end, new_line, cursor, line_end:N or "
                      "line_start:N")
    if at == "cursor":
        return ""
    if at == "end":
        return "{Ctrl}{End}"
    if at == "new_line":           # a new line after the last one — never an empty line
        # skip the line break only when the text is KNOWN to end with one (a surface
        # that tells nothing gets the break: a new line is what was asked for)
        return "{Ctrl}{End}" + ("" if tail.endswith(("\n", "\r")) else "{Enter}")
    line = int(m.group(3))
    if line < 1:
        raise UIError("line numbers start at 1")
    return "{Ctrl}{Home}" + "{Down}" * (line - 1) + ("{End}" if m.group(2) == "end"
                                                     else "{Home}")


def _type_keys(auto: Any, w: Any, el: Any, text: str, at: str = "end",
               tail: str = "") -> str:
    """Keyboard typing into a surface without a Value pattern, at the place `at` names.
    Never blind: the keys go out only when the focus is inside THIS window."""
    _activate(w)
    el.SetFocus()
    time.sleep(0.1)
    if not _focus_inside(auto, w):
        raise UIError("could not move keyboard focus into the target window — refusing "
                      "to type")
    lines = text.replace("\r\n", "\n").split("\n")
    if at == "new_line" and lines and lines[0] == "":
        lines = lines[1:]                   # the new line is made by the place itself
    auto.SendKeys(place_keys(at, tail) + "{Enter}".join(_escape_sendkeys(x) for x in lines),
                  interval=0.005, waitTime=0.1)
    return f"SendKeys(at {at})"


def _perform(auto: Any, c: Any, op: str, text: str | None, w: Any = None,
             at: str = "end", tail: str = "") -> str:
    """Run one op through UIA patterns; returns the method used."""
    el = auto.Control.CreateControlFromElement(c)
    if op == "type":
        if not text:
            raise UIError("type needs text")
        return _type_keys(auto, w, el, text, at, tail)
    if op == "click":
        for pattern, method in (("GetInvokePattern", "Invoke"), ("GetTogglePattern", "Toggle"),
                                ("GetSelectionItemPattern", "Select"),
                                ("GetExpandCollapsePattern", "Expand")):
            try:
                getattr(getattr(el, pattern)(), method)()
                return f"{pattern.removeprefix('Get')}.{method}"
            except Exception:  # noqa: BLE001 - try the next pattern
                continue
        raise UIError("no_pattern: this control takes no UIA click — it needs the "
                      "keyboard or a mouse click in the front window")
    single = {"toggle": ("GetTogglePattern", "Toggle"),
              "select": ("GetSelectionItemPattern", "Select"),
              "expand": ("GetExpandCollapsePattern", "Expand"),
              "collapse": ("GetExpandCollapsePattern", "Collapse"),
              "scroll_into_view": ("GetScrollItemPattern", "ScrollIntoView")}
    if op in single:
        pattern, method = single[op]
        try:
            getattr(getattr(el, pattern)(), method)()
        except Exception as e:  # noqa: BLE001
            raise UIError(f"no_pattern: {op} is not supported by this control ({e})") from None
        return f"{pattern.removeprefix('Get')}.{method}"
    if op == "focus":
        el.SetFocus()
        return "SetFocus"
    if op == "set_text":
        if text is None:
            raise UIError("set_text needs text")
        try:
            el.GetValuePattern().SetValue(text)
        except Exception as e:  # noqa: BLE001
            raise UIError(f"no_pattern: this field takes no direct text ({e})") from None
        return "ValuePattern.SetValue"
    raise UIError(f"unknown op {op!r} (allowed: {', '.join(OPS)})")


def target(observation_id: str, element: int) -> dict[str, Any]:
    """What an action would hit, checked against the screen NOW (for the core's
    permission/safety decision — the same check runs again right before the input)."""
    require_unlocked()
    obs = recall(observation_id)
    want = obs["elements"].get(element)
    if want is None:
        raise UIError(f"stale_target: observation has no element {element}")
    with _uia() as auto:
        w, now = _window_now(auto, obs, want.get("hwnd"))
        c = find_again(want, now)
        proc = _proc_name(obs["pid"])
        return {"element": {**{k: c[k] for k in ("type", "name", "automation_id", "value",
                                                  "enabled")},
                            "password": bool(c.get("password"))},
                "window": {"title": w.Name, "process": proc, "class": win32_class(w),
                           "is_shell": is_shell_window(proc, win32_class(w), w.Name,
                                                       f"{c['name']} {c['automation_id']}")}}


def _window_now(auto: Any, obs: dict[str, Any], hwnd: int | None = None
                ) -> tuple[Any, list[dict[str, Any]]]:
    """The element's window now (its own pop-up, or the main window) and its controls."""
    try:
        w = auto.ControlFromHandle(hwnd or obs["hwnd"])
        alive = w is not None and w.Exists(0, 0)
    except Exception:  # noqa: BLE001 - a destroyed window raises COMError
        alive = False
    if not alive:
        raise UIError("stale_target: the window is gone — observe again")
    if w.ProcessId != obs["pid"] or process_signature(obs["pid"]) != obs["created"]:
        raise UIError("stale_target: the window now belongs to another program")
    return w, _collect(auto, w, keep=True)


def act(observation_id: str, element: int, op: str, text: str | None = None,
        at: str = "end") -> dict[str, Any]:
    require_unlocked()
    if op not in OPS:
        raise UIError(f"unknown op {op!r} (allowed: {', '.join(OPS)})")
    t0 = time.perf_counter()
    obs = recall(observation_id)
    want = obs["elements"].get(element)
    if want is None:
        raise UIError(f"stale_target: observation has no element {element}")
    with input_lease(), _uia() as auto:
        w, now = _window_now(auto, obs, want.get("hwnd"))
        c = find_again(want, now)            # the last check, right before the input
        if not c["enabled"] and op not in ("focus", "scroll_into_view"):
            raise UIError(f"disabled: {c['name']!r} is disabled")
        proc = _proc_name(obs["pid"])
        if op in ("set_text", "click", "type") and is_shell_window(
                proc, win32_class(w), w.Name, f"{c['name']} {c['automation_id']}"):
            raise UIError("shell_target: this window runs what is typed — refused here")
        el = auto.Control.CreateControlFromElement(c["_el"])
        before = {**_state(el), **_window_state(auto, w)}
        content_before = content_signature(now)
        method = _perform(auto, c["_el"], op, text, w, at, str(c.get("tail") or ""))
        time.sleep(SETTLE_SECONDS)
        after = {**_state(el), **_window_state(auto, w)}
        content_changed = w.Exists(0, 0) and content_signature(
            _collect(auto, w)) != content_before
    changed = after != before or content_changed or not after.get("exists", True)
    effect = "changed" if changed else "noop"
    if op == "set_text":
        effect = "changed" if after.get("value") == text else "noop"
    if op == "type":
        effect = "changed" if content_changed else "noop"
    return {"op": op, "element": {"id": element, "type": c["type"], "name": c["name"]},
            "transport_ok": True, "delivered": True, "effect": effect, "method": method,
            "before": before, "after": after, "failure_code": None,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000)}
