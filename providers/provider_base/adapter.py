"""Base class every provider adapter implements (plan §8.1)."""

from __future__ import annotations

import abc
import time
from dataclasses import dataclass

from providers.provider_base.types import (
    HealthState,
    ProviderError,
    ProviderRequest,
    ProviderResult,
    ResultStatus,
    Usage,
)


@dataclass
class Health:
    state: HealthState
    detail: str
    checked_at: float


class ProviderAdapter(abc.ABC):
    """`name` matches the key in providers.yaml. Adapters never execute PC
    actions themselves — they return plans/answers; the Execution Layer acts
    (plan §9.1 side-effect safety)."""

    name: str = ""
    #: What this adapter can do at all (the router intersects this with config).
    capabilities: frozenset[str] = frozenset()

    def __init__(self) -> None:
        self.health = Health(HealthState.UNAVAILABLE, "not checked yet", 0.0)

    def _set(self, state: HealthState, detail: str) -> Health:
        self.health = Health(state, detail, time.time())
        return self.health

    @abc.abstractmethod
    async def check_health(self) -> Health: ...

    @abc.abstractmethod
    async def _complete(self, request: ProviderRequest) -> ProviderResult: ...

    async def complete(self, request: ProviderRequest) -> ProviderResult:
        """Never raises for provider failures: they come back as an ERROR result
        with an error category, so the router can decide what to do."""
        t0 = time.perf_counter()
        try:
            return await self._complete(request)
        except ProviderError as e:
            return ProviderResult(ResultStatus.ERROR, self.name, error_category=e.category,
                                  error=str(e), retry_after_seconds=e.retry_after,
                                  usage=Usage(seconds=time.perf_counter() - t0))

    async def aclose(self) -> None:  # noqa: B027 - optional hook; default has nothing to close
        return None


class NotImplementedAdapter(ProviderAdapter):
    """Placeholder so the provider exists in the architecture from day one
    (plan §8.2) while its real adapter is built in a later phase."""

    def __init__(self, name: str, capabilities: frozenset[str], arrives: str,
                 disabled: bool = False) -> None:
        super().__init__()
        self.name = name
        self.capabilities = capabilities
        self.arrives = arrives
        self.disabled = disabled

    async def check_health(self) -> Health:
        if self.disabled:
            return self._set(HealthState.DISABLED, "disabled in providers.yaml")
        return self._set(HealthState.UNAVAILABLE, f"adapter not built yet ({self.arrives})")

    async def _complete(self, request: ProviderRequest) -> ProviderResult:
        from providers.provider_base.types import ErrorCategory
        raise ProviderError(ErrorCategory.MODEL_UNAVAILABLE,
                            f"{self.name}: adapter not built yet ({self.arrives})")
