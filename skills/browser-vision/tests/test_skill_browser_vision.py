from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core.skills.api import PolicyDenied
from tests.mocks.browser import FakeBrowser
from tests.mocks.skills import make_skill_env

SID = "d" * 32


def site(element: Any) -> FakeBrowser:
    page = {"ok": True, "session_id": SID, "url": "https://example.com/", "title": "Ex"}
    return FakeBrowser({f"/v1/sessions/{SID}/describe_xy": {"ok": True, "element": element},
                        f"/v1/sessions/{SID}/click_xy": page})


def clicked(b: FakeBrowser) -> bool:
    return any(p.endswith("/click_xy") for p, _ in b.calls)


def test_identified_harmless_element_is_clicked_without_asking(tmp_path: Path) -> None:
    b = site({"tag": "CANVAS", "text": "player"})
    s = make_skill_env(tmp_path, {"browser": b})
    r = s.call("browser-vision", "click_xy", {"session_id": SID, "x": 1000, "y": 270,
                                               "target": "green play button"})
    assert r.ok and clicked(b) and s.tasks.buttons == [] and r.untrusted


@pytest.mark.parametrize("element", [{"tag": "BUTTON", "text": "Buy now"},
                                     {"tag": "A", "text": "Delete account"},
                                     None])                  # nothing identifiable
def test_consequential_or_unknown_point_needs_approval(tmp_path: Path, element: Any) -> None:
    b = site(element)
    s = make_skill_env(tmp_path, {"browser": b})
    r, _ = s.call_with_approval("browser-vision", "click_xy",
                                {"session_id": SID, "x": 10, "y": 10, "target": "icon"},
                                approve=False)
    assert r is None and not clicked(b)


def test_dangerous_target_description_alone_needs_approval(tmp_path: Path) -> None:
    b = site({"tag": "DIV", "text": ""})
    s = make_skill_env(tmp_path, {"browser": b})
    s.call_with_approval("browser-vision", "click_xy",
                         {"session_id": SID, "x": 10, "y": 10, "target": "the pay button"},
                         approve=False)
    assert not clicked(b)


def test_point_outside_the_page_is_refused(tmp_path: Path) -> None:
    b = site({"tag": "DIV"})
    s = make_skill_env(tmp_path, {"browser": b})
    with pytest.raises(PolicyDenied):
        s.call("browser-vision", "click_xy", {"session_id": SID, "x": 5000, "y": 10})
    assert b.calls == []
