"""Phase 14 failover engine: circuit breaker (plan §8.6), history-aware
ranking (§8.9), handoff to the next provider (§8.5) and breaker state that
survives a restart (provider_state table)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from core.config import load_config
from core.config.schema import CircuitBreakerSection
from core.db.engine import make_sessionmaker
from core.db.migrate import check_and_migrate
from models.router import ProviderRouter
from models.router.circuit import CircuitBreaker, ProviderHistory
from providers.provider_base import ErrorCategory, HealthState, ProviderRequest
from tests.mocks.providers import FakeAdapter

APP_DIR = Path(__file__).resolve().parents[2]
NAMES = ("openai_codex", "google_antigravity", "gemini_api", "anthropic_claude", "ollama_local")


@pytest.fixture(scope="module")
def cfg():  # type: ignore[no-untyped-def]
    return load_config(APP_DIR / "config", APP_DIR.parent)


@pytest.fixture
def history(tmp_path: Path) -> ProviderHistory:
    mig = check_and_migrate(tmp_path / "agent.db", tmp_path / "bk")
    return ProviderHistory(make_sessionmaker(mig.engine))


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


def req(task_type: str = "coding", **kw: Any) -> ProviderRequest:
    return ProviderRequest(task_id=1, task_type=task_type, user_request="fix it", **kw)


def router(cfg, history: ProviderHistory | None = None, clock: Clock | None = None,  # type: ignore[no-untyped-def]
           **adapters: FakeAdapter) -> ProviderRouter:
    a: dict[str, Any] = {n: FakeAdapter(n, state=HealthState.UNAVAILABLE) for n in NAMES}
    a["ollama_local"] = FakeAdapter("ollama_local", state=HealthState.HEALTHY)
    a.update(adapters)
    r = ProviderRouter(cfg.providers, a, history)
    if clock is not None:
        r.breaker.clock = clock
    return r


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


# ------------------------------------------------------------ circuit breaker

def test_three_transient_failures_open_the_circuit_with_growing_backoff() -> None:
    clock = Clock()
    b = CircuitBreaker(CircuitBreakerSection(transient_failure_threshold=3,
                                             cooldown_backoff_minutes=[10, 20, 30]), clock=clock)
    assert not b.failure("x", ErrorCategory.SERVER_ERROR)
    assert not b.failure("x", ErrorCategory.TIMEOUT)
    assert b.failure("x", ErrorCategory.SERVER_ERROR)                 # 3rd in a row → open
    assert b.open_for("x") == 600
    clock.now += 601                                                   # cooldown over → probe
    assert b.open_for("x") == 0 and b.in_probe("x")
    assert b.failure("x", ErrorCategory.TIMEOUT)                       # failed probe: re-open
    assert b.open_for("x") == 1200                                     # 20 min
    clock.now += 1201
    b.failure("x", ErrorCategory.TIMEOUT)
    assert b.open_for("x") == 1800                                     # 30 min
    clock.now += 1801
    b.failure("x", ErrorCategory.TIMEOUT)
    assert b.open_for("x") == 1800                                     # stays at the last step
    clock.now += 1801
    b.success("x")                                                     # healthy probe → back
    assert b.open_for("x") == 0 and not b.in_probe("x")
    b.failure("x", ErrorCategory.SERVER_ERROR)
    b.failure("x", ErrorCategory.SERVER_ERROR)
    assert b.failure("x", ErrorCategory.SERVER_ERROR) and b.open_for("x") == 600  # reset


def test_usage_limit_opens_at_once_for_the_providers_retry_after() -> None:
    b = CircuitBreaker(CircuitBreakerSection(transient_failure_threshold=3,
                                             cooldown_backoff_minutes=[10, 20, 30]),
                       clock=Clock())
    assert b.failure("x", ErrorCategory.RATE_LIMIT, retry_after=45 * 60, detail="limit")
    assert b.open_for("x") == 45 * 60
    assert not b.failure("y", ErrorCategory.AUTH)                       # not a breaker matter
    assert not b.failure("y", ErrorCategory.UNSAFE)
    assert b.open_for("y") == 0


def test_router_stops_calling_a_failing_provider(cfg) -> None:  # type: ignore[no-untyped-def]
    clock = Clock()
    codex = FakeAdapter("openai_codex", [ErrorCategory.SERVER_ERROR] * 3 + ["codex again"])
    ag = FakeAdapter("google_antigravity", ["google 1", "google 2", "google 3"])
    r = router(cfg, clock=clock, openai_codex=codex, google_antigravity=ag)
    first = run(r.complete(req()))
    assert first.provider == "google_antigravity"
    assert len(codex.requests) == 3                  # 1 call + 2 server-error retries → open
    second = run(r.complete(req()))
    assert second.provider == "google_antigravity" and len(codex.requests) == 3  # skipped
    assert "circuit open" in " ".join(r.describe())
    clock.now += 601                                 # cooldown over: probed, then used again
    third = run(r.complete(req()))
    assert third.provider == "openai_codex" and third.answer == "codex again"
    assert r.breaker.open_for("openai_codex") == 0 and not r.breaker.in_probe("openai_codex")


def test_breaker_state_survives_a_restart(cfg, history: ProviderHistory) -> None:  # type: ignore[no-untyped-def]
    codex = FakeAdapter("openai_codex", [ErrorCategory.RATE_LIMIT])
    run(router(cfg, history, openai_codex=codex,
               google_antigravity=FakeAdapter("google_antigravity")).complete(req()))
    # a new router (service restart) reads the state back from the database
    codex2 = FakeAdapter("openai_codex", ["should not be called"])
    r2 = router(cfg, history, openai_codex=codex2,
                google_antigravity=FakeAdapter("google_antigravity", ["from google"]))
    assert r2.breaker.open_for("openai_codex") > 0
    assert run(r2.complete(req())).provider == "google_antigravity" and codex2.requests == []


def test_every_call_is_recorded_and_a_mostly_failing_provider_ranks_lower(  # type: ignore[no-untyped-def]
        cfg, history: ProviderHistory) -> None:
    for _ in range(3):
        history.record("openai_codex", "coding", 1, "ERROR", "SERVER_ERROR", 1.0)
    history.record("openai_codex", "coding", 1, "OK", None, 1.0)
    rate, n = history.success_rate("openai_codex")
    assert n == 4 and rate == 0.25
    r = router(cfg, history, openai_codex=FakeAdapter("openai_codex"),
               google_antigravity=FakeAdapter("google_antigravity", ["google"]))
    assert [c.provider for c in r.ranked("coding")][:2] == ["google_antigravity", "openai_codex"]
    ok = run(r.complete(req()))
    assert ok.provider == "google_antigravity"
    assert history.success_rate("google_antigravity") == (1.0, 1)          # recorded


# --------------------------------------------------------------------- handoff

def test_next_provider_gets_a_handoff_not_a_fresh_start(cfg) -> None:  # type: ignore[no-untyped-def]
    codex = FakeAdapter("openai_codex", [ErrorCategory.RATE_LIMIT])
    ag = FakeAdapter("google_antigravity", ["done"])
    r = run(router(cfg, openai_codex=codex, google_antigravity=ag).complete(
        req(previous_attempt="tests failed: test_mean")))
    assert r.ok and r.provider == "google_antigravity"
    handed = ag.requests[0].previous_attempt or ""
    assert handed.startswith("tests failed: test_mean")                    # kept
    assert "openai_codex" in handed and "do not start over" in handed


def test_custom_handoff_adds_the_callers_state(cfg) -> None:  # type: ignore[no-untyped-def]
    seen: list[str] = []

    async def handoff(request: ProviderRequest, failed: str, result: Any) -> ProviderRequest:
        seen.append(f"{failed}:{result.error_category}")
        from dataclasses import replace
        return replace(request, context=request.context + "\nDIFF: +1 line")

    codex = FakeAdapter("openai_codex", [ErrorCategory.TIMEOUT, ErrorCategory.TIMEOUT])
    ag = FakeAdapter("google_antigravity", ["done"])
    run(router(cfg, openai_codex=codex, google_antigravity=ag).complete(req(), handoff=handoff))
    assert seen == ["openai_codex:TIMEOUT"] and "DIFF: +1 line" in ag.requests[0].context


def test_simple_tasks_still_never_go_to_the_cloud(cfg) -> None:  # type: ignore[no-untyped-def]
    local = FakeAdapter("ollama_local", [ErrorCategory.SERVER_ERROR] * 3)
    r = router(cfg, ollama_local=local,
               google_antigravity=FakeAdapter("google_antigravity", ["cloud"]))
    out = run(r.complete(req("intent_classification")))
    assert not out.ok and out.provider == "none"          # no escalation to a cloud provider


def test_sign_in_problem_alerts_once_until_it_works_again(cfg, caplog) -> None:  # type: ignore[no-untyped-def]
    """Plan §8.7 auth → provider off + Telegram alert (CRITICAL). Audit 2026-10-15:
    alert from health checks too, and only once per problem."""
    import logging
    codex = FakeAdapter("openai_codex", [ErrorCategory.AUTH, ErrorCategory.AUTH, "fine"])
    r = router(cfg, openai_codex=codex, google_antigravity=FakeAdapter("google_antigravity"))
    with caplog.at_level(logging.CRITICAL):
        run(r.complete(req()))
        codex.state = HealthState.HEALTHY
        codex._set(HealthState.HEALTHY, "probe says ok")
        run(r.complete(req()))                                    # fails again: no 2nd alert
        r.note_health("openai_codex", HealthState.AUTH_REQUIRED, "not signed in")
    alerts = [x for x in caplog.records if "sign-in needed" in x.getMessage()]
    assert len(alerts) == 1
    codex._set(HealthState.HEALTHY, "ok")
    assert run(r.complete(req())).provider == "openai_codex"     # works → alert state reset
    with caplog.at_level(logging.CRITICAL):
        r.note_health("openai_codex", HealthState.AUTH_REQUIRED, "signed out again")
    assert len([x for x in caplog.records if "sign-in needed" in x.getMessage()]) == 2


def test_task_budgets_come_from_config(cfg) -> None:  # type: ignore[no-untyped-def]
    """Plan §58 (audit 2026-10-15): limits per task type live in providers.yaml."""
    from providers.provider_base import Limits
    local = FakeAdapter("ollama_local", ["ok"])
    r = router(cfg, ollama_local=local)
    run(r.complete(req("intent_classification", limits=Limits(timeout_seconds=999,
                                                              max_output_tokens=9999))))
    sent = local.requests[0].limits
    want = cfg.providers.task_budgets["intent_classification"]
    assert sent.timeout_seconds == want.timeout_seconds
    assert sent.max_output_tokens == want.max_output_tokens
    assert set(cfg.providers.task_budgets) >= {"quick_answer", "coding", "summarization"}


def test_critical_memory_sends_heavy_local_work_to_the_cloud(cfg) -> None:  # type: ignore[no-untyped-def]
    """Plan §17H 'critical → route_heavy_local_to_cloud' (audit 2026-10-15)."""
    level = {"now": "normal"}
    a: dict[str, Any] = {n: FakeAdapter(n) for n in NAMES}
    r = ProviderRouter(cfg.providers, a, pressure=lambda: level["now"])
    assert r.ranked("vision")[0].provider == "ollama_local"         # local first normally
    level["now"] = "critical"
    assert r.ranked("vision")[0].provider == "gemini_api"           # cloud while critical
    assert r.ranked("quick_answer")[0].provider == "ollama_local"   # light work stays local


def test_a_slow_provider_loses_a_little_rank(cfg, history: ProviderHistory) -> None:  # type: ignore[no-untyped-def]
    for _ in range(5):
        history.record("openai_codex", "coding", 1, "OK", None, 400.0)      # ~7 min each
    r = router(cfg, history)
    scores = {c.provider: c.priority for c in r.capabilities.candidates("coding")}
    assert scores["openai_codex"] - scores["google_antigravity"] == 10       # config gap
    assert history.median_seconds("openai_codex") == 400.0
    assert [c.provider for c in r.ranked("coding")][0] == "openai_codex"     # 10 pts max


def test_resource_levels_follow_the_thresholds(cfg) -> None:  # type: ignore[no-untyped-def]
    from core.resources import level_for
    rc = cfg.resources
    assert level_for(rc, 50, 40) == "normal"
    assert level_for(rc, rc.ram_percent.moderate, None) == "moderate"
    assert level_for(rc, 50, rc.vram_percent.high) == "high"
    assert level_for(rc, rc.ram_percent.critical, 10) == "critical"
