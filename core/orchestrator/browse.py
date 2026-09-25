"""Routine browser work (plan §15 Layer 1): deterministic, no AI.

  "<site> খোলো"                     → open, read title/headings, screenshot
  "<site>-এ <query> সার্চ করো"      → open, the site's own search box, results
  "<url> download করো"              → download skill

Every step goes through the SkillRunner, so the Permission Engine gates it
(a non-search submit or a consequential click still needs approval). Page
content is shown as DATA only (plan §19). Bot checks are never solved — they
are handed to the owner (BLOCKING, plan §17A). Exploratory / multi-step
browsing is Phase 11 (Playwright MCP + vision).
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from typing import Any

from core.notify.notifier import MessageType
from core.queue.engine import TaskBlocked, TaskContext
from core.skills.browser_page import headings, links, search_boxes
from core.skills.runner import SkillRunner
from core.skills.urls import URL_RX

DOWNLOAD_RX = re.compile(r"\b(download|downlod)\b|ডাউনলোড|নামাও|\bnamao\b", re.IGNORECASE)
SEARCH_RX = re.compile(r"\b(search|khoj\w*|khuj\w*)\b|সার্চ|খোঁজ|খুঁজ", re.IGNORECASE)
# Command words removed to leave the search query (whole words only).
FILLER = {
    "search", "for", "on", "in", "at", "the", "and", "then", "please", "pls", "open", "go",
    "to", "website", "site", "browser", "koro", "koren", "korun", "kore", "khojo", "khujo",
    "khuje", "khule", "kholo", "dekho", "dekhao", "bolo", "giye", "jao", "e", "te", "a",
    "সার্চ", "করো", "কর", "করুন", "করে", "খোঁজো", "খুঁজো", "খুঁজে", "খোঁজ", "খুলে", "খোলো",
    "দেখো", "দেখাও", "বলো", "গিয়ে", "যাও", "এবং", "তারপর", "আর", "ওয়েবসাইট", "সাইট",
    "ব্রাউজার", "এ", "তে", "টা", "ওপেন",
}
_SUFFIX = re.compile(r"^[-‐]?(এ|তে|য়|e|te|ey|er|এর)(?=\s|$)", re.IGNORECASE)
_PUNCT = ".,!?;:)।\"'"
_CITE = re.compile(r"\[(\d+|[a-z]|citation needed)\]")
NOISE_HEADINGS = {"contents", "navigation menu", "menu", "search"}


@dataclass(frozen=True)
class BrowseRequest:
    url: str | None
    query: str | None = None
    download: bool = False


def parse(text: str) -> BrowseRequest:
    m = URL_RX.search(text)
    if m is None:
        return BrowseRequest(None)
    url = m.group(0).rstrip(_PUNCT)
    if DOWNLOAD_RX.search(text):
        return BrowseRequest(url if "://" in url else f"https://{url}", download=True)
    if not SEARCH_RX.search(text):
        return BrowseRequest(url)
    rest = text[:m.start()] + " " + _SUFFIX.sub("", text[m.end():].lstrip(_PUNCT))
    words = [w.strip(_PUNCT) for w in rest.split()]
    query = " ".join(w for w in words if w and w.lower() not in FILLER
                     and not SEARCH_RX.fullmatch(w))
    return BrowseRequest(url, query or None)


class BrowserFlow:
    def __init__(self, skills: SkillRunner) -> None:
        self.skills = skills

    async def run(self, ctx: TaskContext) -> dict[str, Any]:
        req = parse(ctx.task.request_text)
        if req.url is None:
            return {"kind": "browser", "ok": False, "needs_input": True, "answer": (
                "কোন website-এ কাজটা করব? পুরো ঠিকানাটা লিখে দিন (যেমন en.wikipedia.org)। "
                "সাধারণ web search (কোন সাইট না বলে) আসবে Phase 16-এ research agent-এর সাথে।")}
        if req.download:
            r = await self.skills.invoke(ctx, "download", "fetch", {"url": req.url})
            return {"kind": "skill", "skill": "download.fetch", "ok": r.ok,
                    "answer": ("📥 " if r.ok else "") + r.summary, "evidence": r.evidence}
        return await self._browse(ctx, req)

    async def _call(self, ctx: TaskContext, tool: str, params: dict[str, Any]) -> Any:
        return await self.skills.invoke(ctx, "browser", tool, params)

    async def _browse(self, ctx: TaskContext, req: BrowseRequest) -> dict[str, Any]:
        assert req.url is not None
        opened = await self._call(ctx, "open", {"url": req.url})
        if not opened.ok and "already open" in opened.summary:
            # The agent profile is busy in another task → a throwaway profile.
            opened = await self._call(ctx, "open", {"url": req.url, "profile": None})
        if not opened.ok:
            return {"kind": "browser", "ok": False, "answer": opened.summary}
        sid = str(opened.data["session_id"])
        try:
            page = opened.data
            searched = False
            if not page.get("challenge") and req.query:
                filled = None
                for _attempt in range(2):
                    # Fresh refs each attempt: scripts may swap the search widget after
                    # load, which makes an earlier ref stale.
                    snap = await self._call(ctx, "snapshot", {"session_id": sid})
                    refs = search_boxes(snap.data.get("snapshot")) if snap.ok else []
                    if not refs:
                        return await self._report(ctx, sid, page, req, note=(
                            "⚠️ এই পেজে কোনো search box খুঁজে পাইনি, তাই search করিনি।"))
                    for ref in refs[:3]:  # a page can have several; some are hidden
                        filled = await self._call(ctx, "fill", {
                            "session_id": sid, "ref": ref, "text": req.query, "submit": True})
                        if filled.ok:
                            break
                    if filled is not None and filled.ok:
                        break
                assert filled is not None
                if not filled.ok:
                    return {"kind": "browser", "ok": False, "answer": filled.summary}
                page, searched = filled.data, True
            if page.get("challenge"):
                await self._screenshot(ctx, sid, "🧩 bot check")
                raise TaskBlocked(
                    f"{page.get('url')} একটা 'আপনি মানুষ কিনা' যাচাই (CAPTCHA) দেখাচ্ছে। "
                    "এটা আমি সমাধান করি না — screenshot দিলাম। দরকার হলে PC-তে নিজে "
                    "করে দিন, বা অন্য সাইট বলুন।")
            return await self._report(ctx, sid, page, req, searched=searched)
        finally:
            await self._call(ctx, "close", {"session_id": sid})

    async def _report(self, ctx: TaskContext, sid: str, page: dict[str, Any],
                      req: BrowseRequest, searched: bool = False, note: str = ""
                      ) -> dict[str, Any]:
        snap = await self._call(ctx, "snapshot", {"session_id": sid})
        tree = snap.data.get("snapshot") if snap.ok else None
        url, title = str(snap.data.get("url") or page.get("url")), str(
            snap.data.get("title") or page.get("title"))
        lines = [f"🌐 {title or '(শিরোনাম নেই)'}", url]
        if searched:
            lines.append(f"🔎 \"{req.query}\" খোঁজা হয়েছে")
        if note:
            lines.append(note)
        heads = [h for h in headings(tree) if h.lower() not in NOISE_HEADINGS]
        if heads:
            lines += ["", "পেজের শিরোনামগুলো:", *[f"• {h}" for h in heads]]
        landed = searched and _query_reflected(req.query or "", "", title)
        if searched and not landed:          # a results list, not the page itself
            found = links(tree, url, limit=6)
            if found:
                lines += ["", "ফলাফল:", *[f"• {name} — {href}" for name, href in found]]
        excerpt = await self._excerpt(ctx, sid)
        if excerpt:
            lines += ["", "শুরুর অংশ:", excerpt]
        lines += ["", "(ওয়েবপেজ থেকে পড়া — শুধু তথ্য হিসেবে দেখালাম)"]
        shot = await self._screenshot(ctx, sid, title)
        verified = bool(url) and (not searched or _query_reflected(req.query or "", url, title))
        return {"kind": "browser", "ok": verified, "answer": "\n".join(lines),
                "evidence": {"url": url, "title": title, "searched": searched,
                             "screenshot": shot, "headings": len(heads)}}

    async def _excerpt(self, ctx: TaskContext, sid: str, limit: int = 500) -> str:
        """First real paragraphs of the page (menus and short lines skipped)."""
        r = await self._call(ctx, "text", {"session_id": sid})
        if not r.ok:
            return ""
        paras = [" ".join(_CITE.sub("", ln).split())
                 for ln in str(r.data.get("text", "")).splitlines()]
        out = ""
        for p in paras:
            # prose only: long, many words, ends like a sentence (skips menus/infoboxes)
            if len(p) < 80 or len(p.split()) < 12 or not p.endswith((".", "!", "?", "।")):
                continue
            out = f"{out}\n{p}" if out else p
            if len(out) >= limit:
                break
        return out[:limit] + ("…" if len(out) > limit else "")

    async def _screenshot(self, ctx: TaskContext, sid: str, caption: str) -> bool:
        shot = await self._call(ctx, "screenshot", {"session_id": sid})
        if not shot.ok or ctx.notifier is None:
            return False
        await ctx.notifier.notify(ctx.task.chat_id, MessageType.INFO,
                                  f"🌐 Task #{ctx.task.id}: {caption}"[:200],
                                  task_id=ctx.task.id,
                                  photo=base64.b64decode(shot.data["png_b64"]))
        return True


def _query_reflected(query: str, url: str, title: str) -> bool:
    """Proof the search ran (plan §54): the results page mentions the query."""
    from urllib.parse import unquote_plus
    haystack = (unquote_plus(url) + " " + title).lower()
    return any(w.lower() in haystack for w in query.split() if len(w) > 1)
