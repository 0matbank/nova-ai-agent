"""Capability registry (plan §8.8, §8.9): which providers may serve a task
type, in which order. There is no global "main AI" (plan §8)."""

from __future__ import annotations

from dataclasses import dataclass

from core.config.schema import ProvidersConfig
from providers.provider_base import ProviderAdapter

# Never escalate these to a (limited / paid) cloud provider via fallback.
LOCAL_ONLY = frozenset({"simple", "offline", "intent_classification"})


@dataclass(frozen=True)
class Candidate:
    provider: str
    priority: int
    via: str                      # "priority" | "fallback"


class CapabilityRegistry:
    def __init__(self, config: ProvidersConfig, adapters: dict[str, ProviderAdapter]) -> None:
        self.config = config
        self.adapters = adapters

    def routable(self, name: str) -> bool:
        entry = self.config.providers[name]
        if entry.enabled is not True:            # False, or "optional" = off until configured
            return False
        return not (entry.mode == "api" and not self.config.budget_guard.allow_paid_api_usage)

    def candidates(self, capability: str) -> list[Candidate]:
        out: list[Candidate] = []
        for name, entry in self.config.providers.items():
            adapter = self.adapters.get(name)
            if adapter is None or not self.routable(name):
                continue
            if capability in entry.priority:
                out.append(Candidate(name, entry.priority[capability], "priority"))
            elif ("fallback" in entry.priority and capability in adapter.capabilities
                  and (capability not in LOCAL_ONLY or entry.mode == "local")):
                out.append(Candidate(name, entry.priority["fallback"], "fallback"))
        return sorted(out, key=lambda c: -c.priority)
