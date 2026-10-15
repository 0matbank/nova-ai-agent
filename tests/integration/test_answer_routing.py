"""Owner rule 2026-10-15: quick questions → local first; numbers/dates, current
information, complex requests or a not-sure local answer → Gemini/Antigravity,
silently (one final answer, no visible switching)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from core.config import load_config
from core.orchestrator.answer import COMPLEX, CURRENT, FACT, QUICK, answer, answer_tier
from core.orchestrator.executor import UserRequestExecutor
from core.queue.states import TaskState
from core.router.intent import IntentRouter
from models.router import ProviderRouter
from providers.provider_base import HealthState
from tests.mocks.providers import FakeAdapter
from tests.mocks.skills import SkillEnv, make_skill_env

ALL = frozenset({"quick_answer", "fact_check", "current_info", "reasoning", "simple",
                 "intent_classification", "fallback", "summarization"})


@pytest.mark.parametrize(("text", "tier"), [
    ("চাঁদে প্রথম কে হাঁটেন? এক লাইনে বলো", QUICK),
    ("Who wrote Gitanjali?", QUICK),
    ("bangladesh er rajdhani ki", QUICK),
    ("আলোর গতি কত?", FACT),
    ("How many districts are in Bangladesh?", FACT),
    ("পদ্মা সেতু কোন সালে চালু হয়?", FACT),
    ("আজকের ঢাকার আবহাওয়া কেমন?", CURRENT),
    ("What is the latest iPhone?", CURRENT),
    ("dollar er rate koto ajke", CURRENT),
    ("Explain why the sky is blue", COMPLEX),
    ("Python আর JavaScript-এর তুলনা করো", COMPLEX),
    ("ami ekta business plan chai, " + "details " * 40, COMPLEX),
])
def test_tier_from_wording(text: str, tier: str) -> None:
    assert answer_tier(text) == tier


def router(**adapters: FakeAdapter) -> ProviderRouter:
    cfg = load_config()
    a: dict[str, Any] = {n: FakeAdapter(n, state=HealthState.UNAVAILABLE, capabilities=ALL)
                         for n in cfg.providers.providers}
    a.update(adapters)
    return ProviderRouter(cfg.providers, a)


def fake(name: str, script: list[Any]) -> FakeAdapter:
    return FakeAdapter(name, script, capabilities=ALL)


def ask(r: ProviderRouter, text: str) -> Any:
    return asyncio.run(answer(r, 1, text, "be brief", "ctx"))


def test_quick_question_stays_local() -> None:
    local, gem = fake("ollama_local", ["নীল আর্মস্ট্রং।"]), fake("gemini_api", ["x"])
    got = ask(router(ollama_local=local, gemini_api=gem), "চাঁদে প্রথম কে হাঁটেন?")
    assert got.result.provider == "ollama_local" and got.tier == QUICK and not got.escalated
    assert gem.requests == []
    assert "UNSURE" in local.requests[0].system            # local may say it is not sure


def test_unsure_local_answer_escalates_silently() -> None:
    local = fake("ollama_local", ["UNSURE"])
    gem = fake("gemini_api", ["Gitanjali was written by Rabindranath Tagore."])
    got = ask(router(ollama_local=local, gemini_api=gem), "Who wrote Gitanjali?")
    assert got.escalated and got.result.provider == "gemini_api"
    assert got.result.answer.startswith("Gitanjali") and "UNSURE" not in got.result.answer
    assert gem.requests[0].task_type == "fact_check"


def test_numbers_go_to_gemini_first_then_antigravity() -> None:
    local = fake("ollama_local", ["should not be asked"])
    gem = fake("gemini_api", ["আলোর গতি প্রায় ২,৯৯,৭৯২ কিমি/সেকেন্ড।"])
    got = ask(router(ollama_local=local, gemini_api=gem,
                     google_antigravity=fake("google_antigravity", ["x"])), "আলোর গতি কত?")
    assert got.result.provider == "gemini_api" and local.requests == []


def test_current_info_goes_to_antigravity_with_search_allowed() -> None:
    ag = fake("google_antigravity", ["আজ ঢাকায় রোদ, ৩২°C (Google search)।"])
    got = ask(router(google_antigravity=ag, gemini_api=fake("gemini_api", ["x"]),
                     ollama_local=fake("ollama_local", ["x"])), "আজকের ঢাকার আবহাওয়া কেমন?")
    req = ag.requests[0]
    assert got.result.provider == "google_antigravity" and req.task_type == "current_info"
    assert "web.search" in req.allowed_tools and "never guess current facts" in req.system


def test_current_info_falls_back_when_cloud_is_down() -> None:
    local = fake("ollama_local", ["এখন live তথ্য আনতে পারছি না।"])
    got = ask(router(ollama_local=local), "dollar er rate koto ajke")
    assert got.result.ok and got.result.provider == "ollama_local"      # always a reply


def test_web_search_is_never_allowed_outside_current_info() -> None:
    ag = fake("google_antigravity", ["ok"])
    ask(router(google_antigravity=ag), "Explain why the sky is blue")
    assert "web.search" not in ag.requests[0].allowed_tools


# ---------------------------------------------------------------- end to end

def setup(tmp_path: Path, **adapters: FakeAdapter) -> SkillEnv:
    s = make_skill_env(tmp_path)
    r = router(**adapters)
    s.tasks.engine.register("user_request", UserRequestExecutor(IntentRouter(r), r, s.runner))
    return s


def test_news_question_is_answered_not_refused(tmp_path: Path) -> None:
    ag = fake("google_antigravity", ["আজকের শিরোনাম: … (সূত্র: Google)"])
    s = setup(tmp_path, google_antigravity=ag,
              ollama_local=fake("ollama_local", ['{"intent": "research"}']))
    tid = int(s.tasks.store.create(title="t", request_text="আজকের খবর কী?",
                                   task_type="user_request", channel="telegram",
                                   chat_id="555").id)
    for _ in range(4):
        if not asyncio.run(s.tasks.engine.run_once()):
            break
    t = s.tasks.store.get(tid)
    assert t.state is TaskState.COMPLETED and "শিরোনাম" in t.result_summary
    assert ag.requests[0].task_type == "current_info"
