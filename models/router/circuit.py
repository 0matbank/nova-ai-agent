"""Circuit breaker + provider history (plan §8.6, §8.9).

- A provider that keeps failing is not called again and again: after
  `transient_failure_threshold` transient failures in a row (timeout, 5xx,
  model unavailable) its circuit opens for 10 → 20 → 30 minutes
  (`cooldown_backoff_minutes`, longer on each trip in a row). A usage limit
  opens it at once — for the provider's own retry-after when it says one.
- When the cooldown is over the router probes the provider's health first;
  one success closes the circuit and resets the backoff.
- Every call is recorded (provider, task type, status, latency) so routing can
  avoid a provider whose recent calls mostly failed ("degraded").
- The state lives in the database (provider_state), so a restart does not
  send work straight back to a provider that was just failing.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from core.config.schema import CircuitBreakerSection
from core.db.models import ProviderCall, ProviderState
from core.log import get_logger
from providers.provider_base import ErrorCategory

_log = get_logger("provider")
TRANSIENT = frozenset({ErrorCategory.TIMEOUT, ErrorCategory.SERVER_ERROR,
                       ErrorCategory.MODEL_UNAVAILABLE, ErrorCategory.UNKNOWN})
RECENT_CALLS = 10          # window for the success rate
DEGRADED_BELOW = 0.5       # success rate under this (with ≥ MIN_CALLS calls) = degraded
MIN_CALLS = 4


@dataclass
class Breaker:
    failures: int = 0            # transient failures in a row
    trips: int = 0               # times opened in a row (drives the backoff step)
    cooldown_until: float = 0.0
    reason: str = ""


class ProviderHistory:
    """DB persistence for calls and breaker state; every method is best effort —
    routing must keep working even if the database is unavailable."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def record(self, provider: str, task_type: str, task_id: int | str | None, status: str,
               category: str | None, seconds: float) -> None:
        try:
            with self.sessions.begin() as s:
                s.add(ProviderCall(provider=provider, task_type=task_type,
                                   task_id=task_id if isinstance(task_id, int) else None,
                                   status=status, error_category=category,
                                   duration_ms=int(seconds * 1000)))
        except Exception as e:  # noqa: BLE001 - history must never break routing
            _log.warning(f"provider history not recorded: {e}")

    def success_rate(self, provider: str) -> tuple[float, int]:
        try:
            with self.sessions() as s:
                rows = s.scalars(select(ProviderCall.status).where(
                    ProviderCall.provider == provider).order_by(
                    ProviderCall.id.desc()).limit(RECENT_CALLS)).all()
        except Exception:  # noqa: BLE001
            return 1.0, 0
        if not rows:
            return 1.0, 0
        return sum(1 for r in rows if r == "OK") / len(rows), len(rows)

    def load(self) -> dict[str, Breaker]:
        try:
            with self.sessions() as s:
                rows = s.scalars(select(ProviderState)).all()
        except Exception:  # noqa: BLE001
            return {}
        return {r.provider: Breaker(r.consecutive_failures, r.trips,
                                    r.cooldown_until.timestamp() if r.cooldown_until else 0.0,
                                    r.reason or "") for r in rows}

    def save(self, provider: str, b: Breaker) -> None:
        until = datetime.fromtimestamp(b.cooldown_until, UTC) if b.cooldown_until else None
        try:
            with self.sessions.begin() as s:
                row = s.get(ProviderState, provider)
                if row is None:
                    row = ProviderState(provider=provider)
                    s.add(row)
                row.consecutive_failures = b.failures
                row.trips = b.trips
                row.cooldown_until = until
                row.reason = b.reason[:300] or None
        except Exception as e:  # noqa: BLE001
            _log.warning(f"provider state not saved: {e}")


class CircuitBreaker:
    def __init__(self, config: CircuitBreakerSection, history: ProviderHistory | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.config = config
        self.history = history
        self.clock = clock
        self.state: dict[str, Breaker] = history.load() if history is not None else {}

    def _get(self, name: str) -> Breaker:
        return self.state.setdefault(name, Breaker())

    def open_for(self, name: str) -> float:
        """Seconds the circuit stays open (0 = closed or cooldown over → probe)."""
        return max(0.0, self._get(name).cooldown_until - self.clock())

    def reason(self, name: str) -> str:
        return self._get(name).reason

    def in_probe(self, name: str) -> bool:
        """Cooldown over but not yet proven healthy again."""
        b = self._get(name)
        return b.cooldown_until > 0 and self.open_for(name) == 0

    def success(self, name: str) -> None:
        b = self._get(name)
        if b.failures or b.trips or b.cooldown_until:
            if b.cooldown_until:
                _log.info(f"{name}: circuit closed — back in the pool",
                          extra={"provider": name, "action": "provider.circuit",
                                 "status": "closed"})
            self.state[name] = Breaker()
            self._save(name)

    def failure(self, name: str, category: ErrorCategory | None,
                retry_after: float | None = None, detail: str = "") -> bool:
        """Record a failed call; True when this opened the circuit."""
        b = self._get(name)
        if category is ErrorCategory.RATE_LIMIT:
            wait = retry_after or self._backoff(b.trips)
            return self._open(name, b, wait, f"usage limit: {detail}"[:300])
        if category not in TRANSIENT:
            return False                         # auth/unsafe/bad request: not a breaker matter
        b.failures += 1
        if b.failures >= self.config.transient_failure_threshold or self.in_probe(name):
            # a failed probe re-opens at once, one backoff step longer
            return self._open(name, b, self._backoff(b.trips),
                              f"{b.failures} failures in a row ({category}): {detail}"[:300])
        self._save(name)
        return False

    def _backoff(self, trips: int) -> float:
        steps = self.config.cooldown_backoff_minutes
        return 60.0 * steps[min(trips, len(steps) - 1)]

    def _open(self, name: str, b: Breaker, seconds: float, reason: str) -> bool:
        b.cooldown_until = self.clock() + seconds
        b.trips += 1
        b.failures = 0
        b.reason = reason
        _log.warning(f"{name}: circuit open for {seconds / 60:.0f} min — {reason}",
                     extra={"provider": name, "action": "provider.circuit", "status": "open"})
        self._save(name)
        return True

    def _save(self, name: str) -> None:
        if self.history is not None:
            self.history.save(name, self._get(name))

    def degraded(self, name: str) -> bool:
        if self.history is None:
            return False
        rate, n = self.history.success_rate(name)
        return n >= MIN_CALLS and rate < DEGRADED_BELOW
