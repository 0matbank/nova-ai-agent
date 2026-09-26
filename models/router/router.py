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
from collections.abc import Awaitable, Callable
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
                 history: ProviderHistory | None = None) -> None:
        self.config = config
        self.adapters = adapters
        self.capabilities = CapabilityRegistry(config, adapters)
        self.history = history
        self.breaker = CircuitBreaker(config.circuit_breaker, history)

    async def _fresh_health(self, adapter: ProviderAdapter, force: bool = False) -> HealthState:
        if force or time.time() - adapter.health.checked_at > HEALTH_TTL_SECONDS:
            await adapter.check_health()
        return adapter.health.state

    def ranked(self, task_type: str) -> list[Candidate]:
        """Candidates by priority; a degraded provider (recent calls mostly
        failed) loses DEGRADED_PENALTY points."""
        cands = self.capabilities.candidates(task_type)
        return sorted(cands, key=lambda c: -(c.priority - (
            DEGRADED_PENALTY if self.breaker.degraded(c.provider) else 0)))

    async def complete(self, request: ProviderRequest,
                       handoff: Handoff | None = None) -> ProviderResult:
        tried: list[str] = []
        noteworthy = False          # a provider that should have served failed / is limited
        for cand in self.ranked(request.task_type):
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
                return result
            opened = self.breaker.failure(adapter.name, cat, result.retry_after_seconds,
                                          result.error or "")
            if cat is ErrorCategory.RATE_LIMIT:
                adapter._set(HealthState.RATE_LIMITED, result.error or "rate limited")
                return result                       # → next provider immediately
            if cat is ErrorCategory.AUTH:
                adapter._set(HealthState.AUTH_REQUIRED, result.error or "auth required")
                _log.critical(f"{adapter.name}: authentication required — provider disabled",
                              extra={"provider": adapter.name, "action": "provider.auth",
                                     "status": "auth_required"})
                return result
            if not opened and cat in retries and used.get(cat, 0) < retries[cat]:
                used[cat] = used.get(cat, 0) + 1
                continue
            return result

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
