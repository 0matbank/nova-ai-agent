"""Provider Router (plan §8.4–§8.9): pick the best HEALTHY provider for the
task type, react to failures by error category. Circuit breaker, quota
awareness and checkpoint handoff arrive with the failover engine (Phase 14)."""

from __future__ import annotations

import time

from core.config.schema import ProvidersConfig
from core.log import get_logger
from models.router.capabilities import CapabilityRegistry
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
HEALTH_TTL_SECONDS = 60
NO_SWITCH = frozenset({ErrorCategory.UNSAFE, ErrorCategory.TOOL_FAILURE,
                       ErrorCategory.BAD_REQUEST})


class ProviderRouter:
    def __init__(self, config: ProvidersConfig, adapters: dict[str, ProviderAdapter]) -> None:
        self.config = config
        self.adapters = adapters
        self.capabilities = CapabilityRegistry(config, adapters)

    async def _fresh_health(self, adapter: ProviderAdapter) -> HealthState:
        if time.time() - adapter.health.checked_at > HEALTH_TTL_SECONDS:
            await adapter.check_health()
        return adapter.health.state

    async def complete(self, request: ProviderRequest) -> ProviderResult:
        tried: list[str] = []
        for cand in self.capabilities.candidates(request.task_type):
            adapter = self.adapters[cand.provider]
            state = await self._fresh_health(adapter)
            if state not in USABLE:
                tried.append(f"{cand.provider}={state}")
                continue
            result = await self._attempt(adapter, request)
            _log.info(f"{request.task_type} via {cand.provider}: {result.status}",
                      extra={"task_id": request.task_id, "provider": cand.provider,
                             "action": "provider.complete", "status": result.status,
                             "duration": round(result.usage.seconds, 2),
                             "error_code": result.error_category})
            if result.ok or result.error_category in NO_SWITCH:
                return result
            tried.append(f"{cand.provider}={result.error_category}")
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
            if result.ok or cat is None:
                return result
            if cat is ErrorCategory.RATE_LIMIT:
                adapter._set(HealthState.RATE_LIMITED, result.error or "rate limited")
                return result                       # → next provider immediately
            if cat is ErrorCategory.AUTH:
                adapter._set(HealthState.AUTH_REQUIRED, result.error or "auth required")
                _log.critical(f"{adapter.name}: authentication required — provider disabled",
                              extra={"provider": adapter.name, "action": "provider.auth",
                                     "status": "auth_required"})
                return result
            if cat in retries and used.get(cat, 0) < retries[cat]:
                used[cat] = used.get(cat, 0) + 1
                continue
            return result

    def describe(self) -> list[str]:
        lines = []
        for name, a in self.adapters.items():
            routable = self.capabilities.routable(name)
            lines.append(f"{name}: {a.health.state} — {a.health.detail}"
                         + ("" if routable else " (not routed)"))
        return lines
