from __future__ import annotations

import pytest

from core.orchestrator.lang import reply_language


@pytest.mark.parametrize(("text", "lang"), [
    ("en.wikipedia.org-এ Dhaka সার্চ করো", "bn"),
    ("example.com open koro", "bn"),
    ("pc er obostha ki", "bn"),
    ("amake ekta golpo bolo", "bn"),
    ("search Rabindranath Tagore on en.wikipedia.org", "en"),
    ("open en.wikipedia.org", "en"),
    ("What is the capital of Bangladesh?", "en"),
    ("https://example.com/koro/ki.pdf download", "en"),     # words inside a URL don't count
])
def test_reply_language(text: str, lang: str) -> None:
    assert reply_language(text) == lang
