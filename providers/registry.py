"""Provider registry: builds one adapter per providers.yaml entry (plan §8.3).

Adding a provider = add its adapter here + a config entry; the orchestrator,
agents, skills and memory never change (plan §8, §8.11)."""

from __future__ import annotations

import asyncio

from core.config import Secrets
from core.config.schema import AppConfig
from providers.gemini_api import GeminiAdapter
from providers.ollama_local import OllamaAdapter
from providers.openai_codex import CodexAdapter
from providers.provider_base import Health, NotImplementedAdapter, ProviderAdapter

PLANNED = {
    "google_antigravity": ("Phase 13", frozenset({"coding", "reasoning", "review"})),
    "gemini_api": ("Phase 14", frozenset({"reasoning", "vision"})),
    "anthropic_claude": ("Phase 14", frozenset({"coding", "reasoning", "review"})),
}


class ProviderRegistry:
    def __init__(self, adapters: dict[str, ProviderAdapter]) -> None:
        self.adapters = adapters

    @classmethod
    def from_config(cls, cfg: AppConfig, secrets: Secrets | None = None) -> ProviderRegistry:
        adapters: dict[str, ProviderAdapter] = {}
        for name, entry in cfg.providers.providers.items():
            disabled = entry.enabled is False
            if name == "ollama_local" and not disabled:
                adapters[name] = OllamaAdapter(cfg.models)
                continue
            if name == "openai_codex" and not disabled:
                adapters[name] = CodexAdapter(cfg.models.alias_sets.get(name, {}).get("default"))
                continue
            if name == "gemini_api" and not disabled:
                key = secrets.get("GEMINI_API_KEY") if secrets is not None else None
                adapters[name] = GeminiAdapter(key, cfg.models.alias_sets.get(name, {}))
                continue
            arrives, caps = PLANNED.get(name, ("a later phase", frozenset()))
            adapters[name] = NotImplementedAdapter(name, caps, arrives, disabled=disabled)
        return cls(adapters)

    async def check_all(self) -> dict[str, Health]:
        results = await asyncio.gather(*(a.check_health() for a in self.adapters.values()))
        return dict(zip(self.adapters, results, strict=True))

    async def aclose(self) -> None:
        for a in self.adapters.values():
            await a.aclose()
