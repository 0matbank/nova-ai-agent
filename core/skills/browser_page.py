"""Reading the Playwright CLI accessibility snapshot (a tree of
{role, name, ref, url, children}; text nodes are plain strings).
Everything read from a page is UNTRUSTED DATA (plan §19)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from urllib.parse import urljoin

SEARCH_WORDS = ("search", "খুঁজ", "খোঁজ", "সার্চ", "find", "query")
INPUT_ROLES = ("searchbox", "combobox", "textbox")


def walk(tree: Any) -> Iterator[dict[str, Any]]:
    """Depth-first, document order."""
    stack = list(tree) if isinstance(tree, list) else [tree]
    while stack:
        node = stack.pop(0)
        if not isinstance(node, dict):
            continue
        yield node
        stack[0:0] = node.get("children") or []


def search_boxes(tree: Any) -> list[str]:
    """refs of the page's search fields, best first: searchboxes, then inputs
    named like search."""
    candidates = [n for n in walk(tree) if n.get("ref") and n.get("role") in INPUT_ROLES
                  and not n.get("ariaHidden")]
    first = [str(n["ref"]) for n in candidates if n.get("role") == "searchbox"]
    named = [str(n["ref"]) for n in candidates if n.get("role") != "searchbox"
             and any(w in str(n.get("name", "")).lower() for w in SEARCH_WORDS)]
    return first + named


def headings(tree: Any, limit: int = 8) -> list[str]:
    out: list[str] = []
    for n in walk(tree):
        name = " ".join(str(n.get("name") or "").split())
        if n.get("role") == "heading" and name and name not in out:
            out.append(name[:160])
        if len(out) >= limit:
            break
    return out


def links(tree: Any, base_url: str, limit: int = 8) -> list[tuple[str, str]]:
    """Content links (inside <main> when the page has one), absolute URLs, de-duplicated."""
    main = next((n for n in walk(tree) if n.get("role") == "main"), None)
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for n in walk(main if main is not None else tree):
        name = " ".join(str(n.get("name") or "").split())
        href = str(n.get("url") or "")
        if n.get("role") != "link" or len(name) < 4 or not href or href.startswith("#"):
            continue
        url = urljoin(base_url, href)
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        out.append((name[:120], url))
        if len(out) >= limit:
            break
    return out
