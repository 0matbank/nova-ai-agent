from __future__ import annotations

import asyncio
import base64
import urllib.parse
from pathlib import Path
from typing import Any

import pytest

from core.ipc.client import WorkerError, WorkerUnavailable
from core.skills.api import PolicyDenied
from tests.mocks.browser import FakeBrowser, browser_available, real_browser_client
from tests.mocks.skills import make_skill_env

SID = "a" * 32
FORM = ("<title>Nova test</title><h1 id=h>Hello</h1>"
        "<input id=n aria-label=Name><button onclick=\"h.innerText='Hi '+n.value\">Greet</button>"
        "<input type=password aria-label=Password><button>Buy now</button>")
PAGE = "data:text/html," + urllib.parse.quote(FORM)


def fake(element: dict[str, Any] | None = None, **routes: Any) -> FakeBrowser:
    page = {"ok": True, "session_id": SID, "url": "https://example.com/", "title": "Example"}
    base: dict[str, Any] = {
        "/v1/sessions": page,
        f"/v1/sessions/{SID}/goto": page,
        f"/v1/sessions/{SID}/click": page,
        f"/v1/sessions/{SID}/fill": page,
        f"/v1/sessions/{SID}/press": page,
        f"/v1/sessions/{SID}/describe": {"ok": True, "element": element},
    }
    base.update(routes)
    return FakeBrowser(base)


@pytest.mark.parametrize("url", ["file:///C:/Windows/win.ini", "chrome://settings",
                                 "http://127.0.0.1:47801/v1/session", "http://localhost:8000",
                                 "http://[::1]/", "http://169.254.169.254/latest/meta-data"])
def test_url_policy_blocks_before_worker(tmp_path: Path, url: str) -> None:
    b = fake()
    s = make_skill_env(tmp_path, {"browser": b})
    with pytest.raises(PolicyDenied):
        s.call("browser", "open", {"url": url})
    with pytest.raises(PolicyDenied):
        s.call("browser", "goto", {"session_id": SID, "url": url})
    assert b.calls == []


def test_open_is_blue_with_page_evidence(tmp_path: Path) -> None:
    s = make_skill_env(tmp_path, {"browser": fake()})
    r = s.call("browser", "open", {"url": "https://example.com"})
    assert r.ok and r.evidence["url"] == "https://example.com/" and r.evidence["title"] == "Example"
    assert r.untrusted and s.tasks.buttons == []


def test_safe_click_runs_dangerous_click_needs_approval(tmp_path: Path) -> None:
    s = make_skill_env(tmp_path, {"browser": fake({"tag": "BUTTON", "text": "Greet"})})
    assert s.call("browser", "click", {"session_id": SID, "ref": "e4"}).ok
    b = fake({"tag": "BUTTON", "text": "Buy now"})
    s = make_skill_env(tmp_path, {"browser": b})
    r, _ = s.call_with_approval("browser", "click", {"session_id": SID, "ref": "e7"})
    assert r is not None and r.ok


def test_password_fill_needs_approval_and_denial_blocks(tmp_path: Path) -> None:
    b = fake({"tag": "INPUT", "type": "password", "text": ""})
    s = make_skill_env(tmp_path, {"browser": b})
    r, _ = s.call_with_approval("browser", "fill", {"session_id": SID, "ref": "e6",
                                                     "text": "x"}, approve=False)
    assert r is None and not any(p.endswith("/fill") for p, _ in b.calls)


def test_unknown_element_fails_closed(tmp_path: Path) -> None:
    s = make_skill_env(tmp_path, {"browser": fake(None)})
    s.call_with_approval("browser", "fill", {"session_id": SID, "ref": "e9", "text": "x"},
                         approve=False)


def test_search_submit_allowed_other_submit_needs_approval(tmp_path: Path) -> None:
    s = make_skill_env(tmp_path, {"browser": fake({"tag": "INPUT", "type": "search",
                                                   "text": ""})})
    assert s.call("browser", "fill", {"session_id": SID, "ref": "e2", "text": "nova",
                                      "submit": True}).ok
    s = make_skill_env(tmp_path, {"browser": fake({"tag": "INPUT", "type": "text",
                                                   "text": "Email"})})
    s.call_with_approval("browser", "fill", {"session_id": SID, "ref": "e2", "text": "a",
                                             "submit": True}, approve=False)
    s.call_with_approval("browser", "press", {"session_id": SID, "key": "Enter"},
                         approve=False)
    assert s.call("browser", "press", {"session_id": SID, "key": "Tab"}).ok


def test_worker_down_is_an_honest_failure(tmp_path: Path) -> None:
    s = make_skill_env(tmp_path, {"browser": FakeBrowser(
        {"/v1/sessions": WorkerUnavailable("down")})})
    r = s.call("browser", "open", {"url": "https://example.com"})
    assert not r.ok and r.data.get("needs_browser_worker")
    r = make_skill_env(tmp_path, {}).call("browser", "open", {})
    assert not r.ok and r.data.get("needs_browser_worker")


def _ref(tree: Any, role: str, name: str) -> str:
    """Find an element's ref in the CLI's accessibility snapshot tree."""
    stack = list(tree) if isinstance(tree, list) else [tree]
    while stack:
        node = stack.pop()
        if node.get("role") == role and node.get("name") == name:
            return str(node["ref"])
        stack.extend(node.get("children") or [])
    raise AssertionError(f"{role} {name!r} not in snapshot: {tree}")


@pytest.mark.e2e
@pytest.mark.skipif(not browser_available(), reason="Playwright CLI / node not installed")
def test_real_browser_form_flow(tmp_path: Path) -> None:
    client, cli = real_browser_client(tmp_path)
    s = make_skill_env(tmp_path, {"browser": client})
    try:
        r = s.call("browser", "open", {"url": PAGE, "profile": "test"})
        assert r.ok, r.summary
        sid = r.data["session_id"]
        assert r.evidence["title"] == "Nova test"
        assert not s.call("browser", "open", {"profile": "test"}).ok     # profile lock

        snap = s.call("browser", "snapshot", {"session_id": sid})
        assert snap.ok and snap.untrusted
        tree = snap.data["snapshot"]
        assert s.call("browser", "fill", {"session_id": sid, "text": "Nova",
                                          "ref": _ref(tree, "textbox", "Name")}).ok
        assert s.call("browser", "click", {"session_id": sid,
                                           "ref": _ref(tree, "button", "Greet")}).ok
        after = s.call("browser", "snapshot", {"session_id": sid})
        assert "Hi Nova" in str(after.data["snapshot"])

        # consequential button and password field are gated by the core
        s.call_with_approval("browser", "click", {"session_id": sid,
                                                  "ref": _ref(tree, "button", "Buy now")},
                             approve=False)
        s.call_with_approval("browser", "fill", {"session_id": sid, "text": "x",
                                                 "ref": _ref(tree, "textbox", "Password")},
                             approve=False)

        shot = s.call("browser", "screenshot", {"session_id": sid})
        assert shot.ok and base64.b64decode(shot.data["png_b64"])[:4] == b"\x89PNG"

        # the worker itself also refuses local URLs (defence in depth)
        with pytest.raises(WorkerError):
            asyncio.run(client.call("POST", f"/v1/sessions/{sid}/goto",
                                    json={"url": "file:///C:/Windows/win.ini"}))
        assert s.call("browser", "close", {"session_id": sid}).ok
        assert cli.sessions == {}
    finally:
        asyncio.run(cli.close_all())
