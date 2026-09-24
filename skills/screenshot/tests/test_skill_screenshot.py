from __future__ import annotations

import base64
import io
import sys
from pathlib import Path

import pytest

from core.ipc.client import WorkerDesktopLocked
from core.skills.desktop import DesktopUnavailable
from tests.mocks.desktop import FakeDesktop, real_desktop_client
from tests.mocks.skills import make_skill_env

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows desktop capture")


def test_real_capture(tmp_path: Path) -> None:
    from PIL import Image
    s = make_skill_env(tmp_path, {"desktop": real_desktop_client(tmp_path)})
    r = s.call("screenshot", "capture", {"preview_width": 640})
    assert r.ok, r.summary
    png = Path(r.evidence["path"])
    try:
        assert png.exists() and r.evidence["width"] > 0 and r.evidence["height"] > 0
        preview = Image.open(io.BytesIO(base64.b64decode(r.data["preview_jpeg_b64"])))
        assert preview.format == "JPEG" and preview.width <= 640
        assert s.tasks.buttons == []                                  # GREEN
    finally:
        png.unlink(missing_ok=True)


def test_locked_pc_parks_instead_of_failing(tmp_path: Path) -> None:
    s = make_skill_env(tmp_path, {"desktop": FakeDesktop(
        {"/v1/screenshot": WorkerDesktopLocked("locked")})})
    with pytest.raises(DesktopUnavailable):
        s.call("screenshot", "capture", {})
