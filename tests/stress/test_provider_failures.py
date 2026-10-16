"""Phase 25 — provider failure simulations on the real router + config:
Codex limit, Google outage, Claude disabled/enabled, internet loss.

Every adapter is a scripted fake; the routing, circuit breaker, priorities and
capabilities are the real ones from config/providers.yaml."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from core.config import load_config
from models.router import ProviderRouter
from providers.provider_base import ErrorCategory, HealthState, ProviderRequest
from tests.mocks.providers import FakeAdapter

APP = Path(__file__).resolve().parents[2]
CLOUD = ("openai_codex", "google_antigravity", "gemini_api", "anthropic_claude")
LOCAL_CAPS = frozenset({"bangla", "coding", "current_info", "fact_check", "fallback",
                        "intent_classification", "offline", "planning", "quick_answer",
                        "reasoning", "review", "simple", "summarization", "vision"})


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(scope="module")
def cfg():  # type: ignore[no-untyped-def]
    return load_config(APP / "config", APP.parent)


def make(cfg, clock: Clock | None = None, **adapters: FakeAdapter) -> ProviderRouter:  # type: ignore[no-untyped-def]
    a: dict[str, Any] = {n: FakeAdapter(n, state=HealthState.HEALTHY) for n in CLOUD}
    a["anthropic_claude"] = FakeAdapter("anthropic_claude", state=HealthState.DISABLED)
    # the real local adapter's capabilities (it can stand in for every text task type)
    a["ollama_local"] = FakeAdapter("ollama_local", ["local answer"] * 50,
                                    state=HealthState.HEALTHY, capabilities=LOCAL_CAPS)
    a.update(adapters)
    r = ProviderRouter(cfg.providers, a)
    if clock is not None:
        r.breaker.clock = clock
    return r


def ask(r: ProviderRouter, task_type: str) -> Any:
    return asyncio.run(r.complete(ProviderRequest(task_id=1, task_type=task_type,
                                                  user_request="x")))


# ------------------------------------------------------------- Codex limit

def test_codex_usage_limit_moves_coding_to_antigravity_and_comes_back(cfg) -> None:  # type: ignore[no-untyped-def]
    clock = Clock()
    codex = FakeAdapter("openai_codex", [ErrorCategory.RATE_LIMIT, "codex is back"])
    agy = FakeAdapter("google_antigravity", ["agy fixed it", "agy again"])
    r = make(cfg, clock, openai_codex=codex, google_antigravity=agy)
    first = ask(r, "coding")
    assert first.ok and first.provider == "google_antigravity"
    second = ask(r, "coding")                          # Codex is not even tried
    assert second.provider == "google_antigravity" and len(codex.requests) == 1
    assert r.breaker.open_for("openai_codex") > 0
    clock.now += 6 * 3600                              # the limit window is over
    third = ask(r, "coding")
    assert third.provider == "openai_codex" and third.answer == "codex is back"


# ------------------------------------------------------------- Google outage

def test_google_outage_falls_back_to_the_local_model(cfg) -> None:  # type: ignore[no-untyped-def]
    down = [ErrorCategory.SERVER_ERROR] * 10
    r = make(cfg, google_antigravity=FakeAdapter("google_antigravity", list(down)),
             gemini_api=FakeAdapter("gemini_api", list(down)))
    for task_type in ("reasoning", "quick_answer", "summarization"):
        res = ask(r, task_type)
        assert res.ok and res.provider == "ollama_local", (task_type, res)


def test_google_outage_with_live_info_is_an_honest_answer(cfg) -> None:  # type: ignore[no-untyped-def]
    """current_info needs the internet: the local model must not invent today's news."""
    from core.orchestrator.answer import NO_LIVE_BN, answer
    down = [ErrorCategory.SERVER_ERROR] * 10
    r = make(cfg, google_antigravity=FakeAdapter("google_antigravity", list(down)),
             gemini_api=FakeAdapter("gemini_api", list(down)),
             ollama_local=FakeAdapter("ollama_local", ["আজকের খবর: (বানানো)"] * 5,
                                      capabilities=LOCAL_CAPS))
    out = asyncio.run(answer(r, 1, "আজকের খবর কী?", "", ""))
    assert out.result.answer == NO_LIVE_BN and "বানানো" not in out.result.answer


# --------------------------------------------------- Claude disabled/enabled

def test_claude_disabled_is_never_called(cfg) -> None:  # type: ignore[no-untyped-def]
    claude = FakeAdapter("anthropic_claude", ["claude"], state=HealthState.HEALTHY)
    r = make(cfg, anthropic_claude=claude)            # config: enabled: false
    for task_type in ("review", "reasoning", "coding"):
        assert ask(r, task_type).provider != "anthropic_claude"
    assert claude.requests == []


def test_claude_enabled_takes_its_place_and_auth_loss_is_skipped(tmp_path: Path) -> None:
    cdir = tmp_path / "config"
    shutil.copytree(APP / "config", cdir)
    p = cdir / "providers.yaml"
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    data["providers"]["anthropic_claude"]["enabled"] = True
    p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    cfg = load_config(cdir, tmp_path / "rt")
    claude = FakeAdapter("anthropic_claude", ["claude review"], state=HealthState.HEALTHY)
    r = make(cfg, anthropic_claude=claude)
    res = ask(r, "review")                            # priority: Claude 100 > Antigravity 90
    assert res.provider == "anthropic_claude" and res.answer == "claude review"
    # the subscription signs out → it is skipped, the next reviewer answers
    claude.state = HealthState.AUTH_REQUIRED
    asyncio.run(claude.check_health())
    assert ask(r, "review").provider != "anthropic_claude"


# ------------------------------------------------------------- internet loss

def test_internet_loss_every_local_capable_task_still_answers(cfg) -> None:  # type: ignore[no-untyped-def]
    offline = {n: FakeAdapter(n, [ErrorCategory.TIMEOUT] * 20) for n in
               ("openai_codex", "google_antigravity", "gemini_api")}
    r = make(cfg, **offline)
    for task_type in ("simple", "quick_answer", "reasoning", "summarization", "planning",
                      "offline", "coding", "review", "fact_check"):
        res = ask(r, task_type)
        assert res.ok and res.provider == "ollama_local", task_type
    # after a few failures the dead providers are skipped instead of waited for
    before = sum(len(a.requests) for a in offline.values())
    for _ in range(5):
        ask(r, "reasoning")
    assert sum(len(a.requests) for a in offline.values()) - before <= 3


def test_internet_loss_telegram_keeps_retrying_and_recovers() -> None:
    """The poller survives an outage (backoff, no crash) and delivers once it is back."""
    from channels.telegram.api import TelegramTransientError
    from channels.telegram.channel import TelegramChannel

    class FlakyApi:
        def __init__(self) -> None:
            self.calls = 0

        async def get_updates(self, offset: Any, timeout: int) -> list[dict[str, Any]]:
            self.calls += 1
            if self.calls <= 3:
                raise TelegramTransientError("network unreachable")
            stop.set()
            return []

        async def get_me(self) -> dict[str, Any]:
            return {}

        async def acknowledge(self, offset: Any) -> None:
            return None

    stop = asyncio.Event()
    api = FlakyApi()
    ch = TelegramChannel.__new__(TelegramChannel)
    ch.api, ch.poll_timeout = api, 1                   # type: ignore[assignment]
    ch.offset, ch.last_poll_ok, ch.connected, ch.polling_since = None, None, False, None

    async def no_wait(stop_: Any, seconds: float) -> None:
        return None
    import channels.telegram.channel as mod
    original = mod._sleep_or_stop
    mod._sleep_or_stop = no_wait                       # type: ignore[assignment]
    ch.acknowledge = lambda: asyncio.sleep(0)          # type: ignore[method-assign]
    try:
        asyncio.run(ch.run(stop))
    finally:
        mod._sleep_or_stop = original                  # type: ignore[assignment]
    assert api.calls == 4 and ch.connected and ch.last_poll_ok is not None
