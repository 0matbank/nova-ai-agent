"""Answering questions fast without giving up accuracy (owner 2026-10-15):

  "Quick/simple question → local Qwen first. Current information, exact factual
   verification, web-dependent questions, complex reasoning or low confidence →
   automatically Gemini/Antigravity. The owner should not see provider switches
   or needless delay — a fast, accurate answer is the priority."

Tiers (picked by deterministic rules, no extra AI call — plan §49.1):
- quick        → `quick_answer`: local qwen3 (~2–3 s, stays in GPU memory). It
                 must reply UNSURE when not confident → silently escalates to fact_check.
- fact_check   → `fact_check`: Gemini API first (~5 s), then Antigravity — exact
                 numbers, dates, statistics, rankings.
- current_info → `current_info`: Antigravity first with Google search allowed for
                 this request only, then Gemini — news, weather, prices, "latest", today.
- complex      → `reasoning`: Antigravity first — long or analytical requests.
Every tier ends in the local model as last resort, so a text reply is always possible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from providers.provider_base import Limits, ProviderRequest, ProviderResult

QUICK, FACT, CURRENT, COMPLEX = "quick", "fact_check", "current_info", "complex"
TASK_TYPE = {QUICK: "quick_answer", FACT: "fact_check", CURRENT: "current_info",
             COMPLEX: "reasoning"}
UNSURE = "UNSURE"


def _rx(*parts: str) -> re.Pattern[str]:
    return re.compile("|".join(parts), re.IGNORECASE)


CURRENT_RX = _rx(
    r"\b(today|tonight|now|right now|currently|current|latest|recent(ly)?|this (week|month|year)|"
    r"news|headlines?|weather|forecast|price|rate|exchange rate|score|live|trending|"
    r"20[2-9]\d)\b",
    r"(আজ|আজকে|এখন|বর্তমান|সর্বশেষ|লেটেস্ট|সাম্প্রতিক|এই\s*(সপ্তাহ|মাস|বছর)|খবর|নিউজ|সংবাদ|"
    r"আবহাওয়া|দাম|রেট|স্কোর|লাইভ|২০[২-৯][০-৯])",
    r"\b(aj|ajke|akhon|ekhon|bortoman|latest|khobor|khabar|dam|rate)\b")
COMPLEX_RX = _rx(
    r"\b(explain|why|analy[sz]e|analysis|compare|comparison|difference between|pros and cons|"
    r"plan|strategy|step[- ]by[- ]step|essay|write (me )?(a|an)|draft|calculate|solve|prove|"
    r"review|summari[sz]e|detail(ed)?|in depth)\b",
    r"(কেন|ব্যাখ্যা|বিশ্লেষণ|তুলনা|পার্থক্য|সুবিধা.*অসুবিধা|পরিকল্পনা|কৌশল|ধাপে\s*ধাপে|"
    r"বিস্তারিত|রচনা|লিখে\s*দাও|হিসাব|সমাধান)",
    r"\b(keno|bujhiye|bistarito|tulona|plan\s*koro|likhe\s*dao|hisab)\b")
FACT_RX = _rx(
    # exact numbers, dates and statistics — where a small local model is most likely to slip
    r"\b(how (many|much|far|long|tall|old|fast|big)|when (was|did|is)|what year|which year|"
    r"population|distance|height|speed|area|percent(age)?|statistics?|gdp)\b",
    r"(কত|কবে|কোন\s*সাল|সালে|জনসংখ্যা|দূরত্ব|উচ্চতা|গতি|আয়তন|শতাংশ)",
    r"\b(koto|kobe|kon\s*sal|sale)\b",
    r"\d")


def answer_tier(text: str) -> str:
    """Pick the tier from the wording alone; long requests count as complex."""
    t = text.strip()
    if CURRENT_RX.search(t):
        return CURRENT
    if len(t) > 220 or COMPLEX_RX.search(t):
        return COMPLEX
    if FACT_RX.search(t):
        return FACT
    return QUICK


@dataclass(frozen=True)
class Answer:
    result: ProviderResult
    tier: str
    escalated: bool


LOCAL_QUICK_RULE = (
    f"Answer in one or two short sentences. If you are not fully sure the answer is "
    f"correct, reply with exactly {UNSURE} and nothing else.")
LIVE_RULE = ("This question may need current information. Use live search if you have it, "
             "and say the date or source briefly. If you cannot get current information, "
             "say so honestly — never guess current facts.")


ESCALATION_LIMITS = Limits(timeout_seconds=25, max_output_tokens=800)


def is_unsure(result: ProviderResult) -> bool:
    a = (result.answer or "").strip()
    return not a or UNSURE in a.upper()[:40]


async def answer(providers: object, task_id: int, text: str, system: str, context: str,
                 lang_note: str = "") -> Answer:
    """Route one question through the right tier; quick answers escalate silently."""
    from models.router import ProviderRouter
    assert isinstance(providers, ProviderRouter)
    tier = answer_tier(text)
    limits = Limits(timeout_seconds=180, max_output_tokens=800)

    def request(t: str, extra: str = "") -> ProviderRequest:
        rules = " ".join(p for p in (system, extra, lang_note) if p)
        return ProviderRequest(
            task_id=task_id, task_type=TASK_TYPE[t], user_request=text, context=context,
            system=rules, limits=limits,
            allowed_tools=("web.search",) if t == CURRENT else ())

    if tier == QUICK:
        first = await providers.complete(request(QUICK, LOCAL_QUICK_RULE))
        if first.ok and not is_unsure(first):
            return Answer(first, QUICK, escalated=False)
        # live 2026-10-23: the silent escalation waited 120 s on one stuck Gemini call
        # (it normally answers in ~5 s) — a quick question's check gets a short limit
        result = await providers.complete(replace(request(FACT), limits=ESCALATION_LIMITS))
        return Answer(result, FACT, escalated=True)
    extra = LIVE_RULE if tier == CURRENT else ""
    result = await providers.complete(request(tier, extra))
    if tier == CURRENT and result.ok and result.provider == "ollama_local":
        # every cloud provider is down: the local model has no live data and invents
        # "today's news" (benchmark 2026-10-15) — say so honestly instead
        result = replace(result, answer=NO_LIVE_BN if _bangla(text) else NO_LIVE_EN,
                         verification_hints=(*result.verification_hints, "no live source"))
    return Answer(result, tier, escalated=False)


NO_LIVE_BN = ("এখন আজকের/লাইভ তথ্য আনার মতো কোনো online AI পাওয়া যাচ্ছে না (Gemini/Antigravity "
              "বন্ধ বা সীমা শেষ), আর local AI-র কাছে আজকের তথ্য নেই — তাই বানিয়ে বলছি না। "
              "একটু পরে আবার জিজ্ঞেস করুন।")
NO_LIVE_EN = ("No online AI is reachable right now for live information (Gemini/Antigravity are "
              "down or out of quota), and the local AI has no current data — so I won't guess. "
              "Please ask again a little later.")


def _bangla(text: str) -> bool:
    from core.orchestrator.lang import reply_language
    return reply_language(text) == "bn"
