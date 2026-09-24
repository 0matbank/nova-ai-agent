"""Phase 0 foundation check, run as part of the pytest suite."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(not (APP_DIR.parent / "secrets").is_dir(),
                    reason="needs the local D:\\Personal-Agent runtime layout")
def test_foundation_and_secrets_boundary() -> None:
    r = subprocess.run([sys.executable, str(APP_DIR / "scripts" / "verify_foundation.py")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
