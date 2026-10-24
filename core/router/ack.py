"""V1.2 (plan §68, MYRA/Dodaios idea): "শুনেছি — এখন X করছি" the moment a request
arrives, before any work. Built from the same fixed rules the router uses — no AI, so it
costs nothing and never claims more than the rules know. Unclear requests get no claim
(None): the generic receipt stays."""

from __future__ import annotations

from typing import Any

from core.router.intent import classify_by_rules

CATEGORY = {
    "research": "🔎 খুঁজে দেখছি", "reminder": "⏰ সময়মতো মনে করিয়ে দেওয়ার ব্যবস্থা করছি",
    "coding": "💻 কোডের কাজ শুরু করছি", "browser": "🌐 browser-এ কাজ করছি",
    "files": "📁 ফাইলের কাজ করছি", "media": "🎬 video/audio-র কাজ করছি",
    "creative": "🎨 creative কাজের পরিকল্পনা করছি", "screenshot": "📸 পর্দা দেখছি",
}
MEDIA = {"play_pause": "⏯ গান/ভিডিও চালু বা থামাচ্ছি", "next": "⏭ পরেরটা চালাচ্ছি",
         "previous": "⏮ আগেরটা চালাচ্ছি", "stop": "⏹ থামাচ্ছি"}


def _device(tool: str, p: dict[str, Any]) -> str:
    what = "volume" if tool == "volume" else "brightness"
    if tool == "media":
        return MEDIA.get(str(p.get("action")), "⏯ media নিয়ন্ত্রণ করছি")
    if p.get("mute") is True:
        return "🔇 mute করছি"
    if p.get("mute") is False:
        return "🔊 unmute করছি"
    if p.get("level") is not None:
        return f"{'🔊' if tool == 'volume' else '🔆'} {what} {p['level']}% করছি"
    if p.get("change"):
        return f"{'🔊' if tool == 'volume' else '🔆'} {what} " + \
            ("বাড়াচ্ছি" if p["change"] > 0 else "কমাচ্ছি")
    return f"{'🔊' if tool == 'volume' else '🔆'} {what} দেখছি"


def ack_for(text: str, apps: Any | None = None) -> str | None:
    """What Nova is about to do, in the owner's language (Bangla), or None if unclear."""
    try:
        hit = classify_by_rules(text, apps)
    except Exception:  # noqa: BLE001 - an acknowledgement must never block the request
        return None
    if hit is None:
        return None
    if hit.skill:
        skill, tool, params = hit.skill
        app = str((params or {}).get("app") or "")
        if skill == "device":
            return _device(tool, params or {})
        if skill == "app-control":
            return {"open": f"🚀 {app} খুলছি", "close": f"❎ {app} বন্ধ করছি",
                    "kill": f"⛔ {app} জোর করে বন্ধ করার অনুমতি চাইব",
                    "view": f"👀 {app}-এ কী আছে দেখছি"}.get(tool)
        if skill == "windows":
            return {"status": "🖥️ PC-র অবস্থা দেখছি", "processes": "📋 কী কী চলছে দেখছি",
                    "open_windows": "🪟 কী কী খোলা আছে দেখছি"}.get(tool)
        if skill == "screenshot":
            return "📸 screenshot নিচ্ছি"
        if skill == "admin":
            return "🛡️ admin কাজ — আগে আপনার অনুমোদন চাইব"
        return None
    if hit.meta.get("inside_app"):
        return f"🖱️ {hit.meta['inside_app']}-এর ভেতরে কাজ শুরু করছি"
    return CATEGORY.get(hit.category)
