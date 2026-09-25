"""URL policy shared by the browser and download skills.

Only http(s) (plus about:blank / data:text/html for pages). Never file://,
browser-internal schemes, or this PC's loopback services — the worker APIs
live there, so a web page must never be able to steer the agent into them.
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

_TLDS = "com|net|org|info|io|ai|app|dev|xyz|co|me|tv|news|gov|edu|bd|in|uk|us"
# An explicit web address in free text: https://… or a bare domain (en.wikipedia.org).
URL_RX = re.compile(
    r"https?://[^\s<>\"'()]+"
    rf"|(?<![\w@.-])(?:[a-z0-9-]+\.)+(?:{_TLDS})(?:\.[a-z]{{2}})?(?![a-z0-9])"
    r"(?:/[^\s<>\"'()]*)?", re.IGNORECASE)


class UrlBlocked(ValueError):
    pass


def check_url(url: str, allow_pages: bool = True) -> str:
    url = url.strip()
    if allow_pages and (url == "about:blank" or url.startswith("data:text/html")):
        return url
    parsed = urlparse(url if "://" in url else f"https://{url}")
    if parsed.scheme not in ("http", "https"):
        raise UrlBlocked(f"URL scheme {parsed.scheme!r} is not allowed")
    host = (parsed.hostname or "").lower()
    if not host:
        raise UrlBlocked("URL has no host")
    if host == "localhost" or host.endswith(".localhost"):
        raise UrlBlocked("local services on this PC are not reachable by the agent")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return parsed.geturl()
    if ip.is_loopback or ip.is_link_local or ip.is_unspecified:
        raise UrlBlocked("local services on this PC are not reachable by the agent")
    return parsed.geturl()
