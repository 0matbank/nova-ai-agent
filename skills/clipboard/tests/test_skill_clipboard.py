"""Clipboard round trip. Runs only when the user's clipboard is empty or holds
plain text, and restores it afterwards; otherwise skips (never lose an
image/rich content the user copied)."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from core.log import add_known_secrets
from tests.mocks.desktop import real_desktop_client
from tests.mocks.skills import SkillEnv, make_skill_env

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows clipboard")

PLAIN_TEXT_FORMATS = {1, 7, 13, 16}   # CF_TEXT, CF_OEMTEXT, CF_UNICODETEXT, CF_LOCALE


def _formats() -> set[int]:
    import win32clipboard as cb
    cb.OpenClipboard()
    try:
        fmts, f = set(), 0
        while True:
            f = cb.EnumClipboardFormats(f)
            if not f:
                return fmts
            fmts.add(f)
    finally:
        cb.CloseClipboard()


@pytest.fixture
def s(tmp_path: Path) -> Iterator[SkillEnv]:
    if _formats() - PLAIN_TEXT_FORMATS:
        pytest.skip("clipboard holds non-text content — refusing to overwrite it")
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "workers" / "desktop-worker"))
    import desktop_clipboard
    original = desktop_clipboard.get_text()
    try:
        yield make_skill_env(tmp_path, {"desktop": real_desktop_client(tmp_path)})
    finally:
        desktop_clipboard.set_text(original["text"] if original["has_text"] else "")


def test_round_trip(s: SkillEnv) -> None:
    r = s.call("clipboard", "set", {"text": "Nova AI ক্লিপবোর্ড test"})
    assert r.ok and r.evidence["verified"]
    got = s.call("clipboard", "get", {})
    assert got.data["text"] == "Nova AI ক্লিপবোর্ড test" and got.untrusted


def test_copied_secret_is_redacted(s: SkillEnv) -> None:
    add_known_secrets(["nova-clip-secret-777"])
    s.call("clipboard", "set", {"text": "token=nova-clip-secret-777"})
    assert "nova-clip-secret-777" not in s.call("clipboard", "get", {}).data["text"]
