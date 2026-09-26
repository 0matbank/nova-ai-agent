"""Phase 8: provider abstraction, Ollama adapter, capability routing, health."""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path

import pytest

from core.config import load_config
from core.config.schema import ModelsConfig, ProvidersConfig
from models.router import CapabilityRegistry, ProviderRouter
from providers.ollama_local import OllamaAdapter
from providers.openai_codex import CodexAdapter
from providers.provider_base import (
    ErrorCategory,
    HealthState,
    NotImplementedAdapter,
    ProviderRequest,
)
from providers.registry import ProviderRegistry
from tests.mocks.providers import FakeAdapter, FakeOllama

APP_DIR = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def cfg():  # type: ignore[no-untyped-def]
    return load_config(APP_DIR / "config", APP_DIR.parent)


def req(task_type: str = "reasoning", **kw) -> ProviderRequest:  # type: ignore[no-untyped-def]
    return ProviderRequest(task_id=1, task_type=task_type, user_request="hi", **kw)


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


# ---------------------------------------------------------------- registry

def test_every_configured_provider_exists_from_day_one(cfg) -> None:  # type: ignore[no-untyped-def]
    reg = ProviderRegistry.from_config(cfg)
    assert set(reg.adapters) == {"openai_codex", "google_antigravity", "gemini_api",
                                 "anthropic_claude", "ollama_local"}
    assert isinstance(reg.adapters["ollama_local"], OllamaAdapter)
    health = run(reg.adapters["anthropic_claude"].check_health())
    assert health.state is HealthState.DISABLED                      # plan §8.2
    assert isinstance(reg.adapters["openai_codex"], CodexAdapter)          # real since Phase 12
    placeholder = run(reg.adapters["google_antigravity"].check_health())
    assert placeholder.state is HealthState.UNAVAILABLE


def test_placeholder_never_answers() -> None:
    a = NotImplementedAdapter("openai_codex", frozenset({"coding"}), "Phase 12")
    r = run(a.complete(req("coding")))
    assert not r.ok and r.error_category is ErrorCategory.MODEL_UNAVAILABLE


# ------------------------------------------------------------ capabilities

def _with(cfg, **changes) -> ProvidersConfig:  # type: ignore[no-untyped-def]
    data = copy.deepcopy(cfg.providers.model_dump(mode="json"))
    for path, value in changes.items():
        name, key = path.split("__")
        data["providers"][name][key] = value
    return ProvidersConfig.model_validate(data)


def _adapters() -> dict[str, FakeAdapter]:
    return {n: FakeAdapter(n) for n in ("openai_codex", "google_antigravity", "gemini_api",
                                        "anthropic_claude", "ollama_local")}


def test_coding_order_and_disabled_claude(cfg) -> None:  # type: ignore[no-untyped-def]
    caps = CapabilityRegistry(cfg.providers, _adapters())
    order = [c.provider for c in caps.candidates("coding")]
    assert order == ["openai_codex", "google_antigravity", "ollama_local"]   # plan §8.8
    assert "anthropic_claude" not in order                                  # disabled


def test_enabling_claude_is_only_a_config_change(cfg) -> None:  # type: ignore[no-untyped-def]
    caps = CapabilityRegistry(_with(cfg, anthropic_claude__enabled=True), _adapters())
    assert [c.provider for c in caps.candidates("review")][0] == "anthropic_claude"


def test_simple_tasks_never_go_to_cloud(cfg) -> None:  # type: ignore[no-untyped-def]
    caps = CapabilityRegistry(cfg.providers, _adapters())
    for cap in ("simple", "offline", "intent_classification"):
        assert [c.provider for c in caps.candidates(cap)] == ["ollama_local"], cap


def test_optional_and_paid_api_not_routed(cfg) -> None:  # type: ignore[no-untyped-def]
    """A paid API provider is never routed while the budget guard is off; only a
    free-tier key (free_tier_only) may be, and only for its own task types."""
    caps = CapabilityRegistry(cfg.providers, _adapters())
    assert "gemini_api" not in [c.provider for c in caps.candidates("reasoning")]
    paid = CapabilityRegistry(_with(cfg, gemini_api__free_tier_only=False), _adapters())
    for cap in ("vision", "summarization", "reasoning"):
        assert "gemini_api" not in [c.provider for c in paid.candidates(cap)], cap


# ------------------------------------------------------------------ router

def _router(cfg, **adapters) -> ProviderRouter:  # type: ignore[no-untyped-def]
    a = _adapters()
    for n, ad in a.items():
        ad.state = HealthState.UNAVAILABLE if n != "ollama_local" else HealthState.HEALTHY
    a.update(adapters)
    return ProviderRouter(cfg.providers, a)


def test_router_skips_unhealthy_and_falls_back_to_local(cfg) -> None:  # type: ignore[no-untyped-def]
    r = run(_router(cfg).complete(req("reasoning")))
    assert r.ok and r.provider == "ollama_local"


def test_timeout_retried_once_then_next(cfg) -> None:  # type: ignore[no-untyped-def]
    ag = FakeAdapter("google_antigravity", [ErrorCategory.TIMEOUT, ErrorCategory.TIMEOUT])
    local = FakeAdapter("ollama_local", ["local answer"])
    r = run(_router(cfg, google_antigravity=ag, ollama_local=local).complete(req("reasoning")))
    assert len(ag.requests) == 2 and r.provider == "ollama_local"


def test_rate_limit_switches_immediately(cfg) -> None:  # type: ignore[no-untyped-def]
    codex = FakeAdapter("openai_codex", [ErrorCategory.RATE_LIMIT])
    ag = FakeAdapter("google_antigravity", ["from google"])
    r = run(_router(cfg, openai_codex=codex, google_antigravity=ag).complete(req("coding")))
    assert r.answer == "from google" and len(codex.requests) == 1
    assert codex.health.state is HealthState.RATE_LIMITED


def test_auth_error_disables_provider(cfg) -> None:  # type: ignore[no-untyped-def]
    codex = FakeAdapter("openai_codex", [ErrorCategory.AUTH])
    r = run(_router(cfg, openai_codex=codex).complete(req("coding")))
    assert codex.health.state is HealthState.AUTH_REQUIRED and r.provider == "ollama_local"


@pytest.mark.parametrize("cat", [ErrorCategory.UNSAFE, ErrorCategory.TOOL_FAILURE])
def test_unsafe_or_tool_failure_never_switches(cfg, cat) -> None:  # type: ignore[no-untyped-def]
    codex = FakeAdapter("openai_codex", [cat])
    local = FakeAdapter("ollama_local")
    r = run(_router(cfg, openai_codex=codex, ollama_local=local).complete(req("coding")))
    assert r.error_category is cat and local.requests == []


def test_nothing_usable(cfg) -> None:  # type: ignore[no-untyped-def]
    dead = FakeAdapter("ollama_local", state=HealthState.UNAVAILABLE)
    r = run(_router(cfg, ollama_local=dead).complete(req("simple")))
    assert not r.ok and "ollama_local=UNAVAILABLE" in (r.error or "")


# ------------------------------------------------------------------ ollama

def _ollama(cfg: object, fake: FakeOllama) -> OllamaAdapter:
    return OllamaAdapter(cfg.models, client=fake.client())  # type: ignore[attr-defined]


def test_ollama_health(cfg) -> None:  # type: ignore[no-untyped-def]
    assert run(_ollama(cfg, FakeOllama()).check_health()).state is HealthState.HEALTHY
    assert run(_ollama(cfg, FakeOllama(installed=[])).check_health()).state \
        is HealthState.DEGRADED
    down = FakeOllama()
    down.fail = ["down"]
    assert run(_ollama(cfg, down).check_health()).state is HealthState.UNAVAILABLE


def test_ollama_chat_uses_alias_and_policy(cfg) -> None:  # type: ignore[no-untyped-def]
    fake = FakeOllama(reply="ঢাকা")
    r = run(_ollama(cfg, fake).complete(req("reasoning", system="be brief")))
    assert r.ok and r.answer == "ঢাকা" and r.model == "qwen3:8b"
    assert r.usage.input_tokens == 11 and r.usage.output_tokens == 7
    body = fake.chats[0]
    assert body["think"] is True and body["stream"] is False   # reasoning → thinking (config)
    assert body["keep_alive"] == "600s" and body["messages"][0]["role"] == "system"


def test_ollama_model_names_come_from_config(cfg) -> None:  # type: ignore[no-untyped-def]
    data = cfg.models.model_dump(mode="json")
    data["alias_sets"]["ollama_models"]["fast_general"] = "llama-x:3b"
    models = ModelsConfig.model_validate(data)
    fake = FakeOllama(installed=["llama-x:3b"])
    r = run(OllamaAdapter(models, client=fake.client()).complete(req("simple")))
    assert r.ok and fake.chats[0]["model"] == "llama-x:3b"          # no code change


def test_heavy_model_loaded_on_demand_only_one_at_a_time(cfg) -> None:  # type: ignore[no-untyped-def]
    data = cfg.models.model_dump(mode="json")
    data["alias_sets"]["ollama_models"]["second_heavy"] = "big-b:20b"
    data["ollama_policy"]["on_demand_aliases"] = ["local_code_review", "second_heavy"]
    models = ModelsConfig.model_validate(data)
    fake = FakeOllama(installed=["qwen3:8b", "deepseek-coder-v2:16b", "big-b:20b"])
    fake.loaded = ["big-b:20b", "qwen3:8b"]
    r = run(OllamaAdapter(models, client=fake.client()).complete(req("review")))
    assert r.ok and fake.chats[0]["model"] == "deepseek-coder-v2:16b"
    assert fake.unloaded == ["big-b:20b"]                 # other heavy model unloaded first
    assert "qwen3:8b" in fake.loaded                      # light default untouched


def test_thinking_is_config_driven(cfg) -> None:  # type: ignore[no-untyped-def]
    fake = FakeOllama(reply='{"intent": "question"}')
    a = _ollama(cfg, fake)
    run(a.complete(req("intent_classification", json_output=True)))
    run(a.complete(req("simple")))
    assert [c["think"] for c in fake.chats] == [False, False]         # fast paths
    run(a.complete(req("reasoning")))
    assert fake.chats[-1]["think"] is True
    assert fake.chats[-1]["options"]["num_predict"] > fake.chats[1]["options"]["num_predict"]
    run(a.complete(req("reasoning", json_output=True)))           # planner steps think too
    assert fake.chats[-1]["think"] is True and fake.chats[-1]["format"] == "json"
    assert fake.chats[-1]["options"]["num_ctx"] == cfg.models.ollama_policy.num_ctx


@pytest.mark.parametrize("fail,cat", [(404, None), ("timeout", ErrorCategory.TIMEOUT),
                                      (500, ErrorCategory.SERVER_ERROR),
                                      ("down", ErrorCategory.SERVER_ERROR)])
def test_ollama_errors_map_to_categories(cfg, fail, cat) -> None:  # type: ignore[no-untyped-def]
    fake = FakeOllama(installed=[] if fail == 404 else ["qwen3:8b"])
    if fail != 404:
        fake.fail = [fail]
    r = run(_ollama(cfg, fake).complete(req("reasoning")))
    assert not r.ok
    assert r.error_category is (cat or ErrorCategory.MODEL_UNAVAILABLE)


@pytest.mark.e2e
def test_real_ollama_bangla_answer(cfg) -> None:  # type: ignore[no-untyped-def]
    async def go():  # type: ignore[no-untyped-def]
        a = OllamaAdapter(cfg.models)
        try:
            if (await a.check_health()).state is not HealthState.HEALTHY:
                return None
            return await a.complete(ProviderRequest(
                task_id=1, task_type="reasoning", user_request="১ যোগ ১ কত? শুধু সংখ্যা লেখো।"))
        finally:
            await a.aclose()
    r = run(go())
    if r is None:
        pytest.skip("local Ollama with the default model is not running")
    assert r.ok and ("2" in r.answer or "২" in r.answer), r.answer
