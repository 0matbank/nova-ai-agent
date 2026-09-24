"""Start/stop the private UIA test window (tests/mocks/uia_target.ps1)."""

from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psutil

SCRIPT = Path(__file__).resolve().parent / "uia_target.ps1"


@contextmanager
def uia_test_window() -> Iterator[str]:
    """Yields the unique window title. Only this process tree is ever killed."""
    import uiautomation as auto

    title = f"Nova UIA Test {uuid.uuid4().hex[:8]}"
    exe = shutil.which("powershell")
    assert exe, "powershell not found"
    proc = subprocess.Popen([exe, "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass",  # noqa: S603
                             "-File", str(SCRIPT), "-Title", title])
    try:
        deadline = time.time() + 20
        with auto.UIAutomationInitializerInThread(debug=False):
            while time.time() < deadline:
                if auto.WindowControl(searchDepth=1, Name=title).Exists(0, 0):
                    break
                time.sleep(0.2)
            else:
                raise RuntimeError("UIA test window did not appear")
        yield title
    finally:
        try:
            parent = psutil.Process(proc.pid)
            for child in parent.children(recursive=True):
                child.kill()
            parent.kill()
        except psutil.NoSuchProcess:
            pass
        proc.wait(timeout=10)
