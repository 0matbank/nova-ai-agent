"""Computer-use test lab (plan §71): private test apps, one per UI technology. Each runs
with a unique window title and only its own process tree is ever stopped — the owner's
apps, browser profile and work are never touched."""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psutil

APPS = Path(__file__).resolve().parent / "cu_apps"
MOCKS = Path(__file__).resolve().parent
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
KINDS = ("winforms", "wpf", "win32_dialog", "tk", "canvas", "web")
EXTRA = ("dup",)                     # action tests (CU-3), not an observer technology


def _command(kind: str, title: str, profile: Path) -> list[str]:
    ps = shutil.which("powershell") or "powershell"
    psargs = [ps, "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File"]
    if kind == "winforms":
        return [*psargs, str(MOCKS / "uia_target.ps1"), "-Title", title]
    if kind == "dup":
        return [*psargs, str(APPS / "dup.ps1"), "-Title", title]
    if kind == "wpf":
        return [*psargs, str(APPS / "wpf.ps1"), "-Title", title]
    if kind == "win32_dialog":
        return [*psargs, str(APPS / "msgbox.ps1"), "-Title", title]
    if kind == "tk":
        return [sys.executable, str(APPS / "tk_app.py"), title]
    if kind == "canvas":
        return [sys.executable, str(APPS / "tk_canvas.py"), title]
    if kind == "web":
        url = (APPS / "web.html").as_uri() + "#" + title.replace(" ", "%20")
        return [str(EDGE), f"--app={url}", f"--user-data-dir={profile}", "--no-first-run",
                "--no-default-browser-check", "--disable-extensions",
                "--force-renderer-accessibility"]
    raise ValueError(kind)


def _window_up(title: str, timeout: float) -> bool:
    import uiautomation as auto
    deadline = time.time() + timeout
    with auto.UIAutomationInitializerInThread(debug=False):
        while time.time() < deadline:
            if auto.WindowControl(searchDepth=1, Name=title).Exists(0, 0):
                return True
            time.sleep(0.2)
    return False


@contextmanager
def lab_app(kind: str, timeout: float = 30) -> Iterator[str]:
    """Start one private test app; yields its unique window title."""
    title = f"Nova Lab {kind} {uuid.uuid4().hex[:6]}"
    profile = Path(tempfile.mkdtemp(prefix="nova-lab-"))
    proc = subprocess.Popen(_command(kind, title, profile))  # noqa: S603
    try:
        if not _window_up(title, timeout):
            raise RuntimeError(f"{kind} test app did not show its window")
        time.sleep(0.5 if kind != "web" else 1.5)        # let it finish drawing
        yield title
    finally:
        with contextlib.suppress(psutil.NoSuchProcess):
            parent = psutil.Process(proc.pid)
            for child in parent.children(recursive=True):
                with contextlib.suppress(psutil.NoSuchProcess):
                    child.kill()
            parent.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=10)
        shutil.rmtree(profile, ignore_errors=True)
