"""Run Nova at NORMAL priority (stdlib only — the supervisor runs without site-packages).

Windows Task Scheduler starts its tasks at a lowered priority: CPU below normal, disk
I/O low, memory low. Measured 2026-10-26: Nova's processes were BELOW_NORMAL with LOW
I/O, and a cold Whisper load took 18-41 s inside Nova against 3.0 s in a normal process
on the same PC — the owner's first spoken word waited for it. Every Nova process raises
itself back to normal (never above) as it starts."""

from __future__ import annotations

import ctypes
import sys

NORMAL_PRIORITY_CLASS = 0x20
PROCESS_IO_PRIORITY = 33          # NtSetInformationProcess class: ProcessIoPriority
IO_PRIORITY_NORMAL = 2
PROCESS_MEMORY_PRIORITY = 0       # SetProcessInformation class: ProcessMemoryPriority
MEMORY_PRIORITY_NORMAL = 5


class _MemoryPriority(ctypes.Structure):
    _fields_ = [("MemoryPriority", ctypes.c_ulong)]


def raise_to_normal() -> list[str]:
    """CPU, disk I/O and memory priority back to normal. Returns what could not be set
    (empty when all three are normal now); never raises."""
    if sys.platform != "win32":
        return []
    failed: list[str] = []
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        me = ctypes.c_void_p(kernel32.GetCurrentProcess())
        if not kernel32.SetPriorityClass(me, NORMAL_PRIORITY_CLASS):
            failed.append("cpu")
        io = ctypes.c_ulong(IO_PRIORITY_NORMAL)
        status = ctypes.WinDLL("ntdll").NtSetInformationProcess(
            me, PROCESS_IO_PRIORITY, ctypes.byref(io), ctypes.sizeof(io))
        if status != 0:
            failed.append("io")
        mem = _MemoryPriority(MEMORY_PRIORITY_NORMAL)
        if not kernel32.SetProcessInformation(me, PROCESS_MEMORY_PRIORITY,
                                              ctypes.byref(mem), ctypes.sizeof(mem)):
            failed.append("memory")
    except (OSError, AttributeError):
        failed.append("unavailable")
    return failed
