"""Provider-neutral contract (plan §8.1). The Master Orchestrator only ever
sees these types; each adapter translates to/from its provider's format."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class HealthState(StrEnum):
    """Plan §8.4."""
    HEALTHY = "HEALTHY"
    BUSY = "BUSY"
    RATE_LIMITED = "RATE_LIMITED"
    COOLDOWN = "COOLDOWN"
    DEGRADED = "DEGRADED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    UNAVAILABLE = "UNAVAILABLE"
    DISABLED = "DISABLED"


USABLE = frozenset({HealthState.HEALTHY, HealthState.BUSY, HealthState.DEGRADED})


class ErrorCategory(StrEnum):
    """Plan §8.7 — drives what the router does next."""
    RATE_LIMIT = "RATE_LIMIT"            # 429 / usage limit → next provider now
    SERVER_ERROR = "SERVER_ERROR"        # 5xx / overloaded → limited retry, then next
    TIMEOUT = "TIMEOUT"                  # retry once, then next
    AUTH = "AUTH"                        # disable provider + alert
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"  # alternative model, then next provider
    TOOL_FAILURE = "TOOL_FAILURE"        # repair the skill, don't switch provider
    UNSAFE = "UNSAFE"                    # never switch provider: user approval/block
    BAD_REQUEST = "BAD_REQUEST"
    UNKNOWN = "UNKNOWN"


class ResultStatus(StrEnum):
    OK = "OK"
    ERROR = "ERROR"


@dataclass(frozen=True)
class Limits:
    timeout_seconds: float = 120.0
    max_output_tokens: int = 2048
    max_cost_usd: float = 0.0            # subscription/local: no metered spend


@dataclass(frozen=True)
class ProviderRequest:
    """Common input (plan §8.1). `context` is the Context Manager's compact
    package — never a full conversation dump (plan §49.2, §58)."""
    task_id: int | str
    task_type: str                        # capability: simple, reasoning, coding, ...
    user_request: str
    context: str = ""
    system: str = ""
    workspace: str | None = None
    allowed_tools: tuple[str, ...] = ()
    risk_level: str = "GREEN"
    checkpoint: dict[str, Any] | None = None
    previous_attempt: str | None = None
    limits: Limits = field(default_factory=Limits)
    json_output: bool = False
    model_alias: str | None = None        # e.g. "intent_classification"
    # PNG/JPEG bytes for vision task types (screenshot understanding, plan §57).
    images: tuple[bytes, ...] = ()


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    cost_usd: float = 0.0


@dataclass(frozen=True)
class ProviderResult:
    """Common output (plan §8.1)."""
    status: ResultStatus
    provider: str
    model: str | None = None
    answer: str = ""
    files_changed: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()
    tool_events: tuple[dict[str, Any], ...] = ()
    session_id: str | None = None
    usage: Usage = field(default_factory=Usage)
    error_category: ErrorCategory | None = None
    error: str | None = None
    retry_after_seconds: float | None = None
    confidence: float | None = None
    verification_hints: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status is ResultStatus.OK


class ProviderError(Exception):
    def __init__(self, category: ErrorCategory, message: str,
                 retry_after: float | None = None) -> None:
        super().__init__(message)
        self.category = category
        self.retry_after = retry_after
