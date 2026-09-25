from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import httpx
import pytest

from core.skills.api import PolicyDenied
from tests.mocks.skills import make_skill_env

BODY = b"%PDF-1.4 nova test" * 1000
SEEN: list[str] = []


def handler(request: httpx.Request) -> httpx.Response:
    SEEN.append(str(request.url))
    if request.url.path == "/hop":
        return httpx.Response(301, headers={"location": "/files/final.pdf"})
    if request.url.path == "/big.bin":
        return httpx.Response(200, content=b"x" * (2 * 1024 * 1024))
    if request.url.path == "/redirect":
        return httpx.Response(302, headers={"location": "http://127.0.0.1:47801/v1/session"})
    if request.url.path == "/missing":
        return httpx.Response(404)
    if request.url.path == "/cd":
        return httpx.Response(200, content=BODY, headers={
            "content-disposition": 'attachment; filename="..\\..\\evil name.pdf"'})
    return httpx.Response(200, content=BODY)


@pytest.fixture(autouse=True)
def fake_web(monkeypatch: pytest.MonkeyPatch) -> None:
    real = httpx.AsyncClient

    def client(*a: Any, **kw: Any) -> httpx.AsyncClient:
        return real(*a, transport=httpx.MockTransport(handler), **kw)
    monkeypatch.setattr(httpx, "AsyncClient", client)


def test_download_is_blue_and_verified(tmp_path: Path) -> None:
    s = make_skill_env(tmp_path)
    r = s.call("download", "fetch", {"url": "https://example.com/files/report.pdf"})
    assert r.ok, r.summary
    dest = Path(r.evidence["path"] if "path" in r.evidence else r.data["path"])
    assert dest.parent == s.cfg.path("downloads_dir") and dest.name == "report.pdf"
    assert r.evidence == {"exists": True, "size": len(BODY),
                          "sha256": hashlib.sha256(BODY).hexdigest()}
    assert s.tasks.buttons == []
    # never overwrites an existing file
    again = s.call("download", "fetch", {"url": "https://example.com/files/report.pdf"})
    assert Path(again.data["path"]).name == "report (1).pdf"


def test_header_filename_is_sanitised(tmp_path: Path) -> None:
    s = make_skill_env(tmp_path)
    r = s.call("download", "fetch", {"url": "https://example.com/cd"})
    p = Path(r.data["path"])
    assert r.ok and p.parent == s.cfg.path("downloads_dir") and ".." not in p.name


def test_size_cap_removes_partial_file(tmp_path: Path) -> None:
    s = make_skill_env(tmp_path)
    r = s.call("download", "fetch", {"url": "https://example.com/big.bin", "max_mb": 1})
    assert not r.ok and "larger" in r.summary
    assert list(s.cfg.path("downloads_dir").iterdir()) == []


def test_blocked_urls_and_redirects(tmp_path: Path) -> None:
    s = make_skill_env(tmp_path)
    for url in ("file:///C:/secret.txt", "http://127.0.0.1:47801/", "http://localhost/x"):
        with pytest.raises(PolicyDenied):
            s.call("download", "fetch", {"url": url})
    SEEN.clear()
    r = s.call("download", "fetch", {"url": "https://example.com/redirect"})
    assert not r.ok and "blocked" in r.summary
    assert not any("127.0.0.1" in u for u in SEEN)          # never even requested
    ok = s.call("download", "fetch", {"url": "https://example.com/hop"})
    assert ok.ok and Path(ok.data["path"]).name == "final.pdf"
    assert not s.call("download", "fetch", {"url": "https://example.com/missing"}).ok
