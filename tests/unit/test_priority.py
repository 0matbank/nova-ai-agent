"""Task Scheduler starts Nova at low CPU / disk priority (measured 2026-10-26: a cold
Whisper load 26.7 s at that priority, 2.9 s at normal). Each Nova process raises itself
back to normal — never above."""

from __future__ import annotations

import sys

import pytest

from core.priority import raise_to_normal


@pytest.mark.skipif(sys.platform != "win32", reason="Windows priorities")
def test_back_to_normal_from_what_task_scheduler_gives() -> None:
    psutil = pytest.importorskip("psutil")
    me = psutil.Process()
    before = me.nice(), me.ionice()
    try:
        me.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        me.ionice(psutil.IOPRIO_LOW)
        assert raise_to_normal() == []
        assert me.nice() == psutil.NORMAL_PRIORITY_CLASS
        assert me.ionice() == psutil.IOPRIO_NORMAL
    finally:
        me.nice(before[0])
        me.ionice(before[1])


def test_never_raises_elsewhere(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert raise_to_normal() == []
