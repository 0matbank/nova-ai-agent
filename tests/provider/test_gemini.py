"""Gemini API adapter (owner 2026-09-25: primary for Bangla page summaries) and
the automatic fallback to local AI — a Gemini problem must never fail the task."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from core.config import load_config
from models.router import CapabilityRegistry, ProviderRouter
from providers.gemini_api import GeminiAdapter
from providers.provider_base import ErrorCategory, HealthState, ProviderRequest
from providers.registry import ProviderRegistry
from tests.mocks.providers import FakeAdapter

APP_DIR = Path(__file__).resolve().parents[2]
ALIASES = {"default": None, "summarization": "gem-a", "summarization_backup": "gem-b"}


@pytest.fixture(scope="module")
def cfg():  # type: ignore[no-untyped-def]
    return load_config(APP_DIR / "config", APP_DIR.parent)


def ok(text: str) -> httpx.Response:
    return httpx.Response(200, json={
        "candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"text": "hidden reasoning", "thought": True}, {"text": text}]}}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}})


class Google:
    """Scripted Gemini REST API: per-model list of responses."""

    def __init__(self, **script: list[httpx.Response]) -> None:
        self.script = script
        self.calls: list[tuple[str, dict[str, Any], str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        model = request.url.path.rsplit("/", 1)[-1].split(":")[0]
        self.calls.append((model, json.loads(request.content),
                           request.headers.get("x-goog-api-key", "")))
        return self.script[model.replace("-", "_")].pop(0)


def gem(google: Google, key: str | None = "k-test") -> GeminiAdapter:
    return GeminiAdapter(SecretStr(key) if key else None, ALIASES,
                         client=httpx.AsyncClient(transport=httpx.MockTransport(google.handler)))


def req(**kw: Any) -> ProviderRequest:
    return ProviderRequest(task_id=1, task_type="summarization", user_request="sum",
                           system="sys", context="<page>x</page>", **kw)


def test_answer_skips_thought_parts_and_sends_key_as_header() -> None:
    g = Google(gem_a=[ok('{"summary": "ঢাকা রাজধানী।"}')])
    r = asyncio.run(gem(g).complete(req(json_output=True)))
    assert r.ok and r.answer == '{"summary": "ঢাকা রাজধানী।"}' and r.model == "gem-a"
    model, body, key = g.calls[0]
    assert key == "k-test" and "k-test" not in json.dumps(body)
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["systemInstruction"]["parts"][0]["text"] == "sys"


def test_overloaded_model_switches_to_backup_model() -> None:
    g = Google(gem_a=[httpx.Response(503)], gem_b=[ok("fine")])
    r = asyncio.run(gem(g).complete(req()))
    assert r.ok and r.model == "gem-b" and [c[0] for c in g.calls] == ["gem-a", "gem-b"]


def test_both_models_down_is_a_server_error() -> None:
    g = Google(gem_a=[httpx.Response(503)], gem_b=[httpx.Response(500)])
    r = asyncio.run(gem(g).complete(req()))
    assert not r.ok and r.error_category is ErrorCategory.SERVER_ERROR


@pytest.mark.parametrize(("status", "cat"), [(429, ErrorCategory.RATE_LIMIT),
                                             (403, ErrorCategory.AUTH),
                                             (400, ErrorCategory.BAD_REQUEST)])
def test_errors_map_to_categories(status: int, cat: ErrorCategory) -> None:
    r = asyncio.run(gem(Google(gem_a=[httpx.Response(status)])).complete(req()))
    assert not r.ok and r.error_category is cat


def test_quota_cooldown_then_primary_again() -> None:
    a = gem(Google(gem_a=[httpx.Response(429)]))
    asyncio.run(a.complete(req()))
    assert asyncio.run(a.check_health()).state is HealthState.COOLDOWN
    a.cooldown_until = time.time() - 1                     # cooldown over
    assert asyncio.run(a.check_health()).state is HealthState.HEALTHY


def test_no_key_is_unavailable_not_a_crash() -> None:
    a = gem(Google(), key=None)
    assert asyncio.run(a.check_health()).state is HealthState.UNAVAILABLE
    assert asyncio.run(a.complete(req())).error_category is ErrorCategory.AUTH


def test_summaries_route_gemini_first_then_local(cfg) -> None:  # type: ignore[no-untyped-def]
    caps = CapabilityRegistry(cfg.providers, ProviderRegistry.from_config(cfg).adapters)
    assert [c.provider for c in caps.candidates("summarization")] == ["gemini_api",
                                                                      "ollama_local"]
    # the budget guard still keeps it away from everything else
    # vision: local qwen3-vl first, Gemini only as fallback (owner 2026-09-25)
    assert [c.provider for c in caps.candidates("vision")] == ["ollama_local", "gemini_api"]
    for cap in ("reasoning", "simple", "intent_classification"):
        assert "gemini_api" not in [c.provider for c in caps.candidates(cap)], cap


@pytest.mark.parametrize("failure", [httpx.Response(429), httpx.Response(403),
                                     httpx.Response(503)])
def test_gemini_failure_falls_back_and_is_audited(cfg, failure: httpx.Response,  # type: ignore[no-untyped-def]
                                                  caplog: pytest.LogCaptureFixture) -> None:
    g = Google(gem_a=[failure] * 5, gem_b=[failure] * 5)
    gemini = gem(g)
    gemini._set(HealthState.HEALTHY, "test")
    local = FakeAdapter("ollama_local", ["স্থানীয় সারাংশ"],
                        capabilities=frozenset({"summarization", "fallback"}))
    local._set(HealthState.HEALTHY, "test")
    adapters: dict[str, Any] = {n: FakeAdapter(n, state=HealthState.UNAVAILABLE)
                                for n in cfg.providers.providers}
    adapters.update(gemini_api=gemini, ollama_local=local)
    caplog.set_level(logging.INFO)
    r = asyncio.run(ProviderRouter(cfg.providers, adapters).complete(req()))
    assert r.ok and r.provider == "ollama_local" and r.answer == "স্থানীয় সারাংশ"
    switch = [rec for rec in caplog.records if getattr(rec, "action", "") == "provider.switch"]
    assert switch and "gemini_api" in switch[0].getMessage()
    assert {rec.name.rsplit(".", 1)[-1] for rec in switch} >= {"audit"}
