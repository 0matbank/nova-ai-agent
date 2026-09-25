"""Download a URL into the downloads folder (BLUE `download`).
Verified by the file actually existing with its size and SHA-256 (plan §54)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx

from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult
from core.skills.urls import UrlBlocked, check_url

MAX_REDIRECTS = 5
_SAFE = re.compile(r"[^\w.\- ()ঀ-৿]+")


class P(Params):
    url: str
    filename: str | None = None
    max_mb: int = 500


def _name(p: P, url: str, content_disposition: str | None) -> str:
    name = p.filename
    if not name and content_disposition:
        m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', content_disposition)
        name = unquote(m.group(1)) if m else None
    if not name:
        name = unquote(Path(urlparse(url).path).name) or "download"
    name = _SAFE.sub("_", name).strip(" ._") or "download"
    return name[:150]


def _free(dest: Path) -> Path:
    """Never overwrite: report.pdf → report (1).pdf."""
    stem, suffix, n = dest.stem, dest.suffix, 1
    while dest.exists() or dest.with_name(dest.name + ".part").exists():
        dest = dest.with_name(f"{stem} ({n}){suffix}")
        n += 1
    return dest


def precheck(p: P, env: ToolEnv) -> None:
    try:
        check_url(p.url, allow_pages=False)
    except UrlBlocked as e:
        raise PolicyDenied(str(e)) from None
    if not 1 <= p.max_mb <= 4096:
        raise PolicyDenied("max_mb must be 1..4096")


async def run(p: P, env: ToolEnv) -> ToolResult:
    folder = env.config.path("downloads_dir")
    folder.mkdir(parents=True, exist_ok=True)
    limit = p.max_mb * 1024 * 1024
    h = hashlib.sha256()
    size = 0
    url = p.url
    async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=15)) as client:
        for _ in range(MAX_REDIRECTS + 1):
            # Every hop is checked BEFORE it is requested: a redirect must never
            # make the agent call this PC's own services (SSRF).
            try:
                check_url(url, allow_pages=False)
            except UrlBlocked as e:
                return ToolResult(False, f"redirected to a blocked address: {e}")
            async with client.stream("GET", url) as r:
                if r.is_redirect and "location" in r.headers:
                    url = str(r.url.join(r.headers["location"]))
                    continue
                if r.status_code != 200:
                    return ToolResult(False, f"HTTP {r.status_code}", {"url": url})
                dest = _free(folder / _name(p, url, r.headers.get("content-disposition")))
                part = dest.with_name(dest.name + ".part")
                with part.open("wb") as fh:
                    async for chunk in r.aiter_bytes(1024 * 256):
                        size += len(chunk)
                        if size > limit:
                            break
                        h.update(chunk)
                        fh.write(chunk)
                if size > limit:
                    part.unlink(missing_ok=True)
                    return ToolResult(False, f"larger than {p.max_mb} MB — stopped")
                part.rename(dest)
                break
        else:
            return ToolResult(False, "too many redirects")
    shown = f"{size / 1024 / 1024:.1f} MB" if size >= 1024 * 1024 else f"{size / 1024:.1f} KB"
    return ToolResult(True, f"downloaded {dest.name} ({shown})",
                      {"path": str(dest), "size": size, "sha256": h.hexdigest()},
                      {"exists": dest.exists(), "size": dest.stat().st_size,
                       "sha256": h.hexdigest()})


TOOL = Tool(name="fetch", params=P, run=run, action="download", target=lambda p: p.url,
            precheck=precheck)
