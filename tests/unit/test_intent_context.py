from __future__ import annotations

import asyncio
import json

import pytest

from core.orchestrator.context import ContextPackage, for_request
from core.router.intent import IntentRouter, classify_by_rules
from models.router import ProviderRouter
from tests.mocks.providers import FakeAdapter


@pytest.mark.parametrize("text,category,skill", [
    ("screenshot dao", "screenshot", ("screenshot", "capture")),
    ("স্ক্রিনশট পাঠাও", "screenshot", ("screenshot", "capture")),
    ("screen ta dekhao", "screenshot", ("screenshot", "capture")),
    ("pc status bolo", "pc_status", ("windows", "status")),
    ("RAM koto use hocche?", "pc_status", ("windows", "status")),
    ("পিসির অবস্থা কেমন", "pc_status", ("windows", "status")),
    ("ki ki cholche pc te", "processes", ("windows", "processes")),
    ("30 minute por download status bolo", "reminder", None),
    ("Click TV repo te player bug fix koro", "coding", None),
    ("chrome e giye website check koro", "browser", None),
    ("notepad kholo", "pc_control", None),
    ("Downloads folder er file gula dekho", "files", None),
    ("facebook caption likhe dao", "social", None),
    ("এখন সবচেয়ে বাংলাদেশের গুরুত্বপূর্ণ নিউজ কোনটি", "research", None),
    ("ajker khobor ki?", "research", None),
    ("what's the latest news in Dhaka", "research", None),
    ("আজকের আবহাওয়া কেমন", "research", None),
    ("ajke 5 tay mone koriye dio", "reminder", None),
    ("Take a skin shot", "screenshot", ("screenshot", "capture")),
    ("বর্তমানে আমার পিসিতে কি অপেন আসে, কি কাস চলছে", "processes", ("windows", "processes")),
    ("এখন পিসির হেল্থ কেমন", "pc_status", ("windows", "status")),
    ("পিসির বর্তমান অবস্থা বলো", "pc_status", ("windows", "status")),
])
def test_rules(text: str, category: str, skill: tuple[str, str] | None) -> None:
    hit = classify_by_rules(text)
    assert hit is not None and hit.category == category and hit.source == "rule"
    assert (hit.skill[:2] if hit.skill else None) == skill


@pytest.mark.parametrize("text", ["বাংলাদেশের রাজধানী কী?", "what is python?",
                                  "kemon acho", "ajke ki korbo bujhte parchi na"])
def test_no_rule_for_open_questions(text: str) -> None:
    assert classify_by_rules(text) is None


def _router(answer: str) -> ProviderRouter:
    from core.config import load_config
    cfg = load_config()
    adapters = {n: FakeAdapter(n) for n in cfg.providers.providers}
    adapters["ollama_local"] = FakeAdapter("ollama_local", [answer])
    return ProviderRouter(cfg.providers, adapters)


def test_ai_fallback_classification() -> None:
    router = _router(json.dumps({"intent": "research", "confidence": 0.8}))
    intent = asyncio.run(IntentRouter(router).classify("ekta jinish jante chai"))
    assert intent.category == "research" and intent.source == "ai" and intent.skill is None
    req = router.adapters["ollama_local"].requests[0]  # type: ignore[attr-defined]
    assert req.task_type == "intent_classification" and req.json_output


@pytest.mark.parametrize("answer", ["not json", json.dumps({"intent": "launch_missiles"})])
def test_bad_ai_output_defaults_to_question(answer: str) -> None:
    intent = asyncio.run(IntentRouter(_router(answer)).classify("hmm"))
    assert intent.category == "question" and intent.source == "default"


def test_no_provider_defaults_to_question() -> None:
    intent = asyncio.run(IntentRouter(None).classify("hmm"))
    assert intent.category == "question"


def test_context_package_is_small_and_ordered() -> None:
    pkg = for_request("Answer", "বাংলায় বলো", risk="GREEN", allowed_skills=["windows"])
    text = pkg.render()
    assert text.index("## Task Goal") < text.index("## Relevant User Instruction") \
        < text.index("## Allowed Skills") < text.index("## Risk Level")
    assert "## Current Diff" not in text                       # empty sections omitted
    with pytest.raises(ValueError):
        ContextPackage().add("Full Chat History", "everything")
    assert len(ContextPackage().add("Known Errors", "x" * 9000).sections["Known Errors"]) == 2000
