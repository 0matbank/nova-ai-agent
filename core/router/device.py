"""V1.2 (plan §68): volume / mute / media / brightness from the owner's own words — fixed
rules, no AI, so it answers in a second. Bangla, Banglish and English. Parameters come
from the words, never guessed: an unclear amount is a fixed step (10)."""

from __future__ import annotations

import re
from typing import Any

STEP = 10
_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")

VOLUME = r"(volume|vol|sound|awaj|awaz|আওয়াজ|আওয়াজ|ভলিউম|ভলুম|সাউন্ড)"
BRIGHT = r"(brightness|bright|alo|ব্রাইটনেস|ব্রাইটনেছ|আলো|উজ্জ্বলতা)"
UP = (r"(barao|bariye|baraw|beshi|increase|up|raise|louder|বাড়াও|বাড়াও|বাড়িয়ে|বাড়িয়ে|"
      r"বেশি|বাড়া)")
DOWN = r"(komao|kom|kamao|decrease|down|lower|quieter|কমাও|কমিয়ে|কম)"
MUTE = (r"(mute|মিউট|(sound|সাউন্ড|ভলিউম)\s*(off|অফ|বন্ধ)|awaj\s*bondho|আওয়াজ\s*বন্ধ|"
        r"আওয়াজ\s*বন্ধ)")
UNMUTE = (r"(unmute|আনমিউট|(sound|সাউন্ড|ভলিউম)\s*(on|অন|চালু)|awaj\s*chalu|"
          r"আওয়াজ\s*চালু|আওয়াজ\s*চালু)")
MUSIC = r"(gaan|gan|song|music|media|গান|মিউজিক|মিডিয়া|মিডিয়া|playback|video|ভিডিও)"
_NUM = re.compile(r"(\d{1,3})\s*(%|percent|পার্সেন্ট|শতাংশ)?")


def _rx(p: str) -> re.Pattern[str]:
    return re.compile(p, re.IGNORECASE)


def _amount(text: str) -> int | None:
    m = _NUM.search(text)
    return min(100, int(m.group(1))) if m else None


def device_command(text: str) -> tuple[str, str, dict[str, Any]] | None:
    t = text.translate(_DIGITS)
    if _rx(UNMUTE).search(t):
        return ("device", "volume", {"mute": False})
    if _rx(MUTE).search(t):
        return ("device", "volume", {"mute": True})
    for word, tool in ((VOLUME, "volume"), (BRIGHT, "brightness")):
        if not _rx(word).search(t):
            continue
        n = _amount(t)
        if _rx(UP).search(t):
            return ("device", tool, {"change": n or STEP})
        if _rx(DOWN).search(t):
            return ("device", tool, {"change": -(n or STEP)})
        if n is not None:
            return ("device", tool, {"level": n})
        if _rx(r"(koto|কত|how\s*much|level|ki\s*obostha|অবস্থা)").search(t):
            return ("device", tool, {})                       # a question: just read it
        return None
    if _rx(MUSIC).search(t) or _rx(r"\b(play\s*/\s*pause|play|pause)\b").search(t) and \
            _rx(MUSIC).search(t):
        if _rx(r"(next|porer|পরের|skip)").search(t):
            return ("device", "media", {"action": "next"})
        if _rx(r"(previous|prev|ager|আগের|back)").search(t):
            return ("device", "media", {"action": "previous"})
        if _rx(r"(pause|thamao|থামাও|play|chalao|চালাও|resume|bajao|বাজাও)").search(t):
            return ("device", "media", {"action": "play_pause"})
        if _rx(r"(stop|bondho|বন্ধ)").search(t):
            return ("device", "media", {"action": "stop"})
    return None
