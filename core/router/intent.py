"""Intent & Task Router (plan §49.1): classify a request and decide whether a
deterministic skill can do it or AI is needed. Not a separate heavy AI:
rules first (Bangla / Banglish / English), local model only for the rest.
Commands never go through an LLM (plan §45)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from providers.provider_base import Limits, ProviderRequest

# Categories from plan §49.1.
CATEGORIES = ("question", "pc_status", "screenshot", "processes", "pc_control", "files",
              "coding", "browser", "research", "project", "reminder", "social", "document",
              "communication")


@dataclass(frozen=True)
class Intent:
    category: str
    confidence: float
    source: str                                 # rule | ai | default
    skill: tuple[str, str, dict[str, Any]] | None = None   # deterministic route
    matched: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


def _rx(*parts: str) -> re.Pattern[str]:
    return re.compile("|".join(parts), re.IGNORECASE)


# Order matters: first match wins.
READ_ONLY_SKILLS: dict[str, tuple[str, str, dict[str, Any]]] = {
    "pc_status": ("windows", "status", {}),
    "screenshot": ("screenshot", "capture", {}),
    "processes": ("windows", "processes", {}),
}

RULES: list[tuple[str, re.Pattern[str], tuple[str, str, dict[str, Any]] | None]] = [
    ("screenshot", _rx(r"screen\s*shot", r"\bskin\s*shot\b", r"স্ক্রিনশট", r"স্ক্রিন\s*শট",
                       r"\bscreen\s*(ta\s*)?(dekhao|dekhaw|"
                       r"dikhao|pathao)\b", r"স্ক্রিন\s*দেখাও"),
     ("screenshot", "capture", {})),
    ("processes", _rx(r"\bprocess(es)?\s*(list|gula|গুলো)?\b.*\b(dekhao|bolo|show|list)\b",
                      r"\bki\s*ki\s*(app|program|process)?\s*chol(che|ce)\b", r"কী কী চলছে",
                      r"\btask\s*manager\b", r"\bwhat.*running\b",
                      r"(কি|কী)\s*(কি|কী\s*)?(কাজ|কাস|app|অ্যাপ|প্রোগ্রাম)?\s*(চলছে|চলতেছে|তলছে)",
                      r"(কি|কী)\s*(অপেন|ওপেন|খোলা)\s*(আছে|আসে)"),
     ("windows", "processes", {})),
    ("pc_status", _rx(r"\bpc\s*(er\s*)?(status|obostha|condition|health)\b",
                      # up to two words in between; tolerant of Whisper spellings
                      r"পিসি\S*(\s+\S+){0,2}\s+(অবস্থা|অবস্তা|অবস্হা|স্ট্যাটাস|স্টেটাস|"
                      r"হেল্থ|হেলথ)",
                      r"\b(cpu|ram|gpu|vram|disk|memory)\b.*\b(koto|status|usage|obostha|"
                      r"kemon|left|free|how much)\b",
                      r"\b(koto|how much)\b.*\b(cpu|ram|gpu|disk)\b", r"(র‍্যাম|সিপিইউ).*(কত|অবস্থা)"),
     ("windows", "status", {})),
    ("reminder", _rx(r"\bremind", r"মনে\s*করি(য়|য)ে", r"\bmone\s*kori(ye|e|a)\b", r"\balarm\b",
                     r"\b\d+\s*(minute|min|hour|ghonta|মিনিট|ঘণ্টা)\s*(por|pore|পর)\b",
                     r"\b(protidin|প্রতিদিন|every\s*day|daily)\b"), None),
    # Current events need live sources (plan §38) — a local model cannot know them.
    ("research", _rx(r"\b(news|latest\s+news|headlines?|breaking|current\s+events?|weather)\b",
                     r"(খবর|নিউজ|সংবাদ|সর্বশেষ|লেটেস্ট|আবহাওয়া)",
                     r"\b(khobor|khabar|akhon\s*ki\s*hocche)\b"), None),
    ("coding", _rx(r"\b(code|coding|bug|debug|refactor|compile|build|repo|repository|git|"
                   r"commit|function|script)\b", r"কোড", r"\bfix\s*koro\b"), None),
    ("browser", _rx(r"\b(website|browser|chrome|site|login|url|web\s*page)\b",
                    r"ওয়েবসাইট", r"ব্রাউজার"), None),
    ("files", _rx(r"\b(file|folder|download(s)?\s*folder|rename|zip|unzip)\b", r"ফাইল",
                  r"ফোল্ডার"), None),
    ("pc_control", _rx(r"\b(open|kholo|khol|close|bondho|band|launch|start)\b.*\b(app|notepad|"
                       r"chrome|paint|vs\s*code|calculator|explorer)\b",
                       r"\b(notepad|chrome|paint|calculator)\b.*\b(kholo|khol|open|bondho|close)\b",
                       r"(খোলো|বন্ধ\s*কর)"), None),
    ("document", _rx(r"\b(pdf|docx?|excel|xlsx|spreadsheet|document)\b", r"ডকুমেন্ট"), None),
    ("social", _rx(r"\b(facebook|fb|tiktok|instagram|youtube|caption|reel|post\s*koro)\b"),
     None),
    ("research", _rx(r"\b(research|compare|khuje|search\s*kore)\b", r"খুঁজে", r"তুলনা"), None),
    ("communication", _rx(r"\b(email|mail|message\s*pathao|whatsapp)\b", r"ইমেইল"), None),
]

AI_PROMPT = (
    "Classify the user's request into exactly one category from this list: "
    + ", ".join(CATEGORIES)
    + '. The request may be Bangla, English or Banglish. Reply ONLY with JSON like '
    '{"intent": "question", "confidence": 0.8}.'
)


def classify_by_rules(text: str) -> Intent | None:
    for category, rx, skill in RULES:
        m = rx.search(text)
        if m:
            return Intent(category, 0.9, "rule", skill, matched=m.group(0))
    return None


class IntentRouter:
    def __init__(self, provider_router: Any | None = None) -> None:
        self.providers = provider_router          # models.router.ProviderRouter

    async def classify(self, text: str, task_id: int | str = "system") -> Intent:
        hit = classify_by_rules(text)
        if hit is not None:
            return hit
        if self.providers is not None:
            result = await self.providers.complete(ProviderRequest(
                task_id=task_id, task_type="intent_classification", user_request=text,
                system=AI_PROMPT, json_output=True, model_alias="intent_classification",
                limits=Limits(timeout_seconds=60, max_output_tokens=60)))
            if result.ok:
                try:
                    data = json.loads(result.answer)
                    cat = str(data.get("intent", "")).strip().lower()
                    conf = float(data.get("confidence", 0.5))
                except (ValueError, AttributeError):
                    cat, conf = "", 0.0
                if cat in CATEGORIES:
                    # AI may only route to fixed READ-ONLY skills (harmless if wrong);
                    # anything that acts is chosen by deterministic rules, never by AI.
                    return Intent(cat, max(0.0, min(conf, 1.0)), "ai",
                                  READ_ONLY_SKILLS.get(cat),
                                  meta={"provider": result.provider, "model": result.model})
        return Intent("question", 0.3, "default")
