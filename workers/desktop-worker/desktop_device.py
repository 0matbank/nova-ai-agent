"""Device controls without any AI (plan §68 / V1.2): speaker volume and mute through
Windows Core Audio, media play/pause/next/previous through the standard media keys,
monitor brightness through DDC/CI (external monitors) or WMI (laptop panels).

Runs in the owner's desktop session (the worker): Core Audio belongs to the user
session, and media keys reach whichever player the owner uses — no player is named."""

from __future__ import annotations

import ctypes
import subprocess
import time
from ctypes import wintypes
from typing import Any

from desktop_ui import UIError

# ---------------------------------------------------------------- volume (Core Audio)


def _endpoint() -> Any:
    import comtypes
    from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

    class IAudioEndpointVolume(IUnknown):
        _iid_ = GUID("{5CDF2C82-841E-4546-9722-0CF74078229A}")
        _methods_ = [
            COMMETHOD([], HRESULT, "RegisterControlChangeNotify", (["in"], ctypes.c_void_p)),
            COMMETHOD([], HRESULT, "UnregisterControlChangeNotify",
                      (["in"], ctypes.c_void_p)),
            COMMETHOD([], HRESULT, "GetChannelCount", (["out"], ctypes.POINTER(ctypes.c_uint))),
            COMMETHOD([], HRESULT, "SetMasterVolumeLevel", (["in"], ctypes.c_float),
                      (["in"], ctypes.c_void_p)),
            COMMETHOD([], HRESULT, "SetMasterVolumeLevelScalar", (["in"], ctypes.c_float),
                      (["in"], ctypes.c_void_p)),
            COMMETHOD([], HRESULT, "GetMasterVolumeLevel",
                      (["out"], ctypes.POINTER(ctypes.c_float))),
            COMMETHOD([], HRESULT, "GetMasterVolumeLevelScalar",
                      (["out"], ctypes.POINTER(ctypes.c_float))),
            COMMETHOD([], HRESULT, "SetChannelVolumeLevel", (["in"], ctypes.c_uint),
                      (["in"], ctypes.c_float), (["in"], ctypes.c_void_p)),
            COMMETHOD([], HRESULT, "SetChannelVolumeLevelScalar", (["in"], ctypes.c_uint),
                      (["in"], ctypes.c_float), (["in"], ctypes.c_void_p)),
            COMMETHOD([], HRESULT, "GetChannelVolumeLevel", (["in"], ctypes.c_uint),
                      (["out"], ctypes.POINTER(ctypes.c_float))),
            COMMETHOD([], HRESULT, "GetChannelVolumeLevelScalar", (["in"], ctypes.c_uint),
                      (["out"], ctypes.POINTER(ctypes.c_float))),
            COMMETHOD([], HRESULT, "SetMute", (["in"], wintypes.BOOL), (["in"], ctypes.c_void_p)),
            COMMETHOD([], HRESULT, "GetMute", (["out"], ctypes.POINTER(wintypes.BOOL))),
        ]

    class IMMDevice(IUnknown):
        _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
        _methods_ = [
            COMMETHOD([], HRESULT, "Activate", (["in"], ctypes.POINTER(GUID)),
                      (["in"], wintypes.DWORD), (["in"], ctypes.c_void_p),
                      (["out"], ctypes.POINTER(ctypes.POINTER(IUnknown)))),
        ]

    class IMMDeviceEnumerator(IUnknown):
        _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
        _methods_ = [
            COMMETHOD([], HRESULT, "EnumAudioEndpoints", (["in"], ctypes.c_int),
                      (["in"], wintypes.DWORD), (["out"], ctypes.POINTER(ctypes.c_void_p))),
            COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint", (["in"], ctypes.c_int),
                      (["in"], ctypes.c_int),
                      (["out"], ctypes.POINTER(ctypes.POINTER(IMMDevice)))),
        ]

    comtypes.CoInitialize()
    enum = comtypes.CoCreateInstance(GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}"),
                                     IMMDeviceEnumerator, comtypes.CLSCTX_ALL)
    device = enum.GetDefaultAudioEndpoint(0, 1)          # eRender, eMultimedia
    iid = IAudioEndpointVolume._iid_
    unknown = device.Activate(ctypes.byref(iid), comtypes.CLSCTX_ALL, None)
    return unknown.QueryInterface(IAudioEndpointVolume)


def volume_state() -> dict[str, Any]:
    ep = _endpoint()
    return {"level": round(ep.GetMasterVolumeLevelScalar() * 100), "muted": bool(ep.GetMute())}


def set_volume(level: int | None = None, change: int | None = None,
               mute: bool | None = None) -> dict[str, Any]:
    """level 0–100, or change ±N, and/or mute on/off. Returns before/after (proof)."""
    ep = _endpoint()
    before = {"level": round(ep.GetMasterVolumeLevelScalar() * 100), "muted": bool(ep.GetMute())}
    target = before["level"]
    if level is not None:
        target = level
    elif change is not None:
        target = before["level"] + change
    target = max(0, min(100, int(target)))
    if level is not None or change is not None:
        ep.SetMasterVolumeLevelScalar(target / 100, None)
        if mute is None and before["muted"] and target > 0:
            ep.SetMute(False, None)                      # "volume 40" means hear it
    if mute is not None:
        ep.SetMute(bool(mute), None)
    after = {"level": round(ep.GetMasterVolumeLevelScalar() * 100), "muted": bool(ep.GetMute())}
    return {"before": before, "after": after}


# ---------------------------------------------------------------- media keys

MEDIA_KEYS = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2}
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP = 0x1, 0x2


def media(action: str) -> dict[str, Any]:
    """The standard media key — whatever player the owner uses answers it."""
    vk = MEDIA_KEYS.get(action)
    if vk is None:
        raise UIError(f"unknown media action {action!r} (use {', '.join(MEDIA_KEYS)})")
    user32 = ctypes.windll.user32
    user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY, 0)
    time.sleep(0.02)
    user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)
    import desktop_guard
    desktop_guard.note_injected()
    return {"sent": action}


# ---------------------------------------------------------------- brightness

class _PhysicalMonitor(ctypes.Structure):
    _fields_ = [("handle", wintypes.HANDLE), ("description", wintypes.WCHAR * 128)]


def _ddc_monitors() -> list[_PhysicalMonitor]:
    """Physical monitors that answer DDC/CI (external screens)."""
    user32, dxva2 = ctypes.windll.user32, ctypes.windll.dxva2
    handles: list[int] = []
    proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                   ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    user32.EnumDisplayMonitors(None, None, proc_type(lambda h, *_: handles.append(h) or True), 0)
    out: list[_PhysicalMonitor] = []
    for h in handles:
        count = wintypes.DWORD()
        if not dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(h, ctypes.byref(count)):
            continue
        arr = (_PhysicalMonitor * count.value)()
        if dxva2.GetPhysicalMonitorsFromHMONITOR(h, count.value, arr):
            out.extend(arr)
    return out


def _wmi_brightness(level: int | None) -> int | None:
    """Laptop panels: WMI. None when the screen does not support it."""
    ps = "(Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorBrightness).CurrentBrightness"
    if level is not None:
        ps = ("(Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorBrightnessMethods)."
              f"WmiSetBrightness(1,{int(level)}) | Out-Null; " + ps)
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],  # noqa: S603, S607
                       capture_output=True, text=True, timeout=20, check=False)
    try:
        return int(r.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return None


def brightness(level: int | None = None, change: int | None = None) -> dict[str, Any]:
    """Read (no arguments) or set the brightness of every screen that allows it."""
    dxva2 = ctypes.windll.dxva2
    screens = []
    for mon in _ddc_monitors():
        lo, cur, hi = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
        try:
            if not dxva2.GetMonitorBrightness(mon.handle, ctypes.byref(lo), ctypes.byref(cur),
                                              ctypes.byref(hi)):
                continue
            before = cur.value
            target = before if level is None and change is None else \
                max(lo.value, min(hi.value, level if level is not None else before + (change or 0)))
            if target != before:
                dxva2.SetMonitorBrightness(mon.handle, target)
                time.sleep(0.3)
                dxva2.GetMonitorBrightness(mon.handle, ctypes.byref(lo), ctypes.byref(cur),
                                           ctypes.byref(hi))
            screens.append({"screen": mon.description, "via": "DDC/CI", "before": before,
                            "after": cur.value})
        finally:
            dxva2.DestroyPhysicalMonitor(mon.handle)
    if not screens:
        before_wmi = _wmi_brightness(None)
        if before_wmi is not None:
            target = before_wmi if level is None and change is None else \
                max(0, min(100, level if level is not None else before_wmi + (change or 0)))
            after = _wmi_brightness(target) if target != before_wmi else before_wmi
            screens.append({"screen": "built-in", "via": "WMI", "before": before_wmi,
                            "after": after})
    if not screens:
        raise UIError("no_brightness: this screen does not let Windows change its "
                      "brightness (no DDC/CI or WMI support) — use the monitor's buttons")
    return {"screens": screens}
