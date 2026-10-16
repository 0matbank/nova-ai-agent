"""Provider Router (plan §8.4–§8.9) — the failover engine.

- Picks the best usable provider for the task type (capability registry); no
  global "main AI".
- Reacts by error category (§8.7): usage limit → next provider now; 5xx /
  timeout → limited retry, then next; auth → provider off + Telegram alert
  (CRITICAL log); unsafe / tool failure / bad request → no switch.
- Circuit breaker (§8.6): a provider that keeps failing is skipped for
  10/20/30 min, then probed before it gets work again.
- Quota / history aware (§8.9): a provider whose recent calls mostly failed
  ranks lower ("degraded"); simple tasks never go to the cloud (registry).
- Handoff (§8.5, §9.2): when a provider fails mid-task the next one does not
  start from zero — it gets the previous provider's failure summary, and the
  caller can add the current state (e.g. the coding flow adds the git diff).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Collection
from dataclasses import replace

from core.config.schema import ProvidersConfig
from core.log import get_logger
from models.router.capabilities import Candidate, CapabilityRegistry
from models.router.circuit import CircuitBreaker, ProviderHistory
from providers.provider_base import (
    USABLE,
    ErrorCategory,
    HealthState,
    ProviderAdapter,
    ProviderRequest,
    ProviderResult,
    ResultStatus,
)

_log = get_logger("provider")
_audit = get_logger("audit")
HEALTH_TTL_SECONDS = 60
NO_SWITCH = frozenset({ErrorCategory.UNSAFE, ErrorCategory.TOOL_FAILURE,
                       ErrorCategory.BAD_REQUEST})


# Skipping a provider that is simply not set up (placeholder, no key) is normal
# routing; these states mean something went wrong and are worth recording.
NOTEWORTHY_STATES = frozenset({HealthState.RATE_LIMITED, HealthState.COOLDOWN,
                               HealthState.AUTH_REQUIRED, HealthState.DEGRADED})
DEGRADED_PENALTY = 30       # priority points a mostly-failing provider loses
# plan §17H "critical → route_heavy_local_to_cloud": heavy local work moves to the cloud
HEAVY_LOCAL_TASKS = frozenset({"vision", "coding", "review"})
PRESSURE_PENALTY = 50
# plan §8.9 latency: a mild tie-breaker — at most this many points for a slow provider
MAX_LATENCY_PENALTY = 10
LATENCY_SECONDS_PER_POINT = 30.0

# (request, failed provider, its result) → the request for the next provider
Handoff = Callable[[ProviderRequest, str, ProviderResult], Awaitable[ProviderRequest]]


async def default_handoff(request: ProviderRequest, failed: str,
                          result: ProviderResult) -> ProviderRequest:
    """The next provider continues, it does not restart: tell it what happened."""
    note = (f"Another assistant ({failed}) was working on this and stopped before finishing "
            f"({result.error_category}: {(result.error or '')[:300]}). Continue from the "
            "current state — do not start over, and do not repeat actions already done.")
    prev = f"{request.previous_attempt}\n\n{note}" if request.previous_attempt else note
    return replace(request, previous_attempt=prev)


def _switch(request: ProviderRequest, tried: list[str], used: str) -> None:
    """A fallback happened: the task goes on, but the switch is recorded (plan §8.7)."""
    msg = f"{request.task_type}: provider switch {' → '.join(tried)} → {used}"
    extra = {"task_id": request.task_id, "provider": used, "action": "provider.switch",
             "status": "fallback"}
    _log.warning(msg, extra=extra)
    _audit.info(msg, extra=extra)


class ProviderRouter:
    def __init__(self, config: ProvidersConfig, adapters: dict[str, ProviderAdapter],
                 history: ProviderHistory | None = None,
                 pressure: Callable[[], str] | None = None) -> None:
        self.config = config
        self.adapters = adapters
        self.capabilities = CapabilityRegistry(config, adapters)
        self.history = history
        self.pressure = pressure          # "normal" | "moderate" | "high" | "critical"
        self.breaker = CircuitBreaker(config.circuit_breaker, history)
        self._auth_alerted: set[str] = set()      # one Telegram alert per sign-in problem

    async def _fresh_health(self, adapter: ProviderAdapter, force: bool = False) -> HealthState:
        if force or time.time() - adapter.health.checked_at > HEALTH_TTL_SECONDS:
            await adapter.check_health()
        return adapter.health.state

    def ranked(self, task_type: str) -> list[Candidate]:
        """Candidates by priority, adjusted by recent history (plan §8.9): a degraded
        provider (recent calls mostly failed) loses DEGRADED_PENALTY points, a slow
        one up to MAX_LATENCY_PENALTY, and under critical memory pressure heavy local
        work goes to the cloud (plan §17H)."""
        critical = self.pressure is not None and self.pressure() == "critical"

        def score(c: Candidate) -> float:
            s = float(c.priority)
            if self.breaker.degraded(c.provider):
                s -= DEGRADED_PENALTY
            if self.history is not None:
                median = self.history.median_seconds(c.provider)
                if median is not None:
                    s -= min(MAX_LATENCY_PENALTY, median / LATENCY_SECONDS_PER_POINT)
            if critical and task_type in HEAVY_LOCAL_TASKS and c.provider == "ollama_local":
                s -= PRESSURE_PENALTY
            return s

        return sorted(self.capabilities.candidates(task_type), key=lambda c: -score(c))

    async def complete(self, request: ProviderRequest, handoff: Handoff | None = None,
                       exclude: Collection[str] = ()) -> ProviderResult:
        """`exclude`: providers that must not serve this request — e.g. the one that wrote
        the code must not be its own independent reviewer (plan §8.10)."""
        budget = self.config.task_budgets.get(request.task_type)
        if budget is not None:          # plan §58: limits come from config, not call sites
            request = replace(request, limits=replace(
                request.limits, timeout_seconds=float(budget.timeout_seconds),
                max_output_tokens=budget.max_output_tokens))
        tried: list[str] = []
        noteworthy = False          # a provider that should have served failed / is limited
        for cand in self.ranked(request.task_type):
            if cand.provider in exclude:
                continue
            adapter = self.adapters[cand.provider]
            left = self.breaker.open_for(cand.provider)
            if left > 0:
                tried.append(f"{cand.provider}=CIRCUIT_OPEN({int(left // 60)}m)")
                noteworthy = True
                continue
            probe = self.breaker.in_probe(cand.provider)
            state = await self._fresh_health(adapter, force=probe)
            if state not in USABLE:
                tried.append(f"{cand.provider}={state}")
                if state in NOTEWORTHY_STATES:
                    noteworthy = True
                continue
            result = await self._attempt(adapter, request)
            _log.info(f"{request.task_type} via {cand.provider}: {result.status}",
                      extra={"task_id": request.task_id, "provider": cand.provider,
                             "action": "provider.complete", "status": result.status,
                             "duration": round(result.usage.seconds, 2),
                             "error_code": result.error_category})
            if result.ok or result.error_category in NO_SWITCH:
                if noteworthy:
                    _switch(request, tried, cand.provider)
                return result
            tried.append(f"{cand.provider}={result.error_category}")
            noteworthy = True
            request = await (handoff or default_handoff)(request, cand.provider, result)
        detail = ", ".join(tried) or "no provider is configured for this task type"
        return ProviderResult(ResultStatus.ERROR, "none",
                              error_category=ErrorCategory.MODEL_UNAVAILABLE,
                              error=f"no provider could serve {request.task_type!r}: {detail}")

    async def _attempt(self, adapter: ProviderAdapter, request: ProviderRequest) -> ProviderResult:
        retries = {ErrorCategory.TIMEOUT: self.config.retry.timeout_retries,
                   ErrorCategory.SERVER_ERROR: self.config.retry.server_error_retries}
        used: dict[ErrorCategory, int] = {}
        while True:
            result = await adapter.complete(request)
            cat = result.error_category
            if self.history is not None:
                self.history.record(adapter.name, request.task_type, request.task_id,
                                    str(result.status), str(cat) if cat else None,
                                    result.usage.seconds)
            if result.ok or cat is None:
                self.breaker.success(adapter.name)
                self._auth_alerted.discard(adapter.name)      # signed in again
                return result
            opened = self.breaker.failure(adapter.name, cat, result.retry_after_seconds,
                                          result.error or "")
            if cat is ErrorCategory.RATE_LIMIT:
                adapter._set(HealthState.RATE_LIMITED, result.error or "rate limited")
                return result                       # → next provider immediately
            if cat is ErrorCategory.AUTH:
                adapter._set(HealthState.AUTH_REQUIRED, result.error or "auth required")
                self._alert_auth(adapter.name, result.error or "auth required")
                return result
            if not opened and cat in retries and used.get(cat, 0) < retries[cat]:
                used[cat] = used.get(cat, 0) + 1
                continue
            return result

    def _alert_auth(self, name: str, detail: str) -> None:
        """Plan §8.7: auth error → provider off + Telegram alert — once per problem,
        not on every call or health round (audit 2026-10-15)."""
        if name in self._auth_alerted:
            return
        self._auth_alerted.add(name)
        _log.critical(f"{name}: sign-in needed — provider not used until it works again "
                      f"({detail[:200]})",
                      extra={"provider": name, "action": "provider.auth",
                             "status": "auth_required"})

    def note_health(self, name: str, state: HealthState, detail: str) -> None:
        """Called after each background health round: a provider that turns
        AUTH_REQUIRED is alerted even if no task tried it yet."""
        if state is HealthState.AUTH_REQUIRED and self.capabilities.routable(name):
            self._alert_auth(name, detail)
        elif state is HealthState.HEALTHY:
            self._auth_alerted.discard(name)

    def describe(self) -> list[str]:
        lines = []
        for name, a in self.adapters.items():
            routable = self.capabilities.routable(name)
            left = self.breaker.open_for(name)
            circuit = (f" — circuit open {int(left // 60) + 1} min: {self.breaker.reason(name)}"
                       if left > 0 else "")
            lines.append(f"{name}: {a.health.state} — {a.health.detail}{circuit}"
                         + ("" if routable else " (not routed)"))
        return lines
