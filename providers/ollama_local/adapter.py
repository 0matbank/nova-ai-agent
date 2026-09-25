"""Local Ollama provider (plan §8.2 "Local Ollama", §9A model policy).

- Models come from role aliases in models.yaml — never hard-coded here.
- On-demand heavy models (e.g. local_code_review) are loaded only when asked,
  and at most `max_heavy_models_loaded` are resident: others are unloaded first.
- Idle models unload after `idle_unload_seconds` (Ollama keep_alive).
- Talks only to the loopback Ollama API.
"""

from __future__ import annotations

import base64
import time
from typing import Any

import httpx

from core.config.schema import ModelsConfig
from providers.provider_base import (
    ErrorCategory,
    Health,
    HealthState,
    ProviderAdapter,
    ProviderError,
    ProviderRequest,
    ProviderResult,
    ResultStatus,
    Usage,
)

DEFAULT_URL = "http://127.0.0.1:11434"

# capability / task type → role alias (plan §9A)
TASK_ALIAS = {
    "simple": "fast_general", "offline": "fast_general", "fallback": "fast_general",
    "reasoning": "fast_general", "intent_classification": "intent_classification",
    "summarization": "summarization", "bangla": "bangla_banglish",
    "coding": "local_code_review", "review": "local_code_review",
    "vision": "vision",
}


class OllamaAdapter(ProviderAdapter):
    name = "ollama_local"
    capabilities = frozenset(TASK_ALIAS)

    def __init__(self, models: ModelsConfig, base_url: str = DEFAULT_URL,
                 client: httpx.AsyncClient | None = None) -> None:
        super().__init__()
        self.aliases = models.alias_sets["ollama_models"]
        self.policy = models.ollama_policy
        self.base = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(300, connect=5))

    async def aclose(self) -> None:
        await self._client.aclose()

    # --------------------------------------------------------------- models

    def model_for(self, request: ProviderRequest) -> tuple[str, str]:
        alias = request.model_alias or TASK_ALIAS.get(request.task_type,
                                                      self.policy.default_alias)
        model = self.aliases.get(alias) or self.aliases[self.policy.default_alias]
        assert model is not None
        return alias, model

    def is_heavy(self, alias: str) -> bool:
        return alias in self.policy.on_demand_aliases

    async def _get(self, path: str) -> dict[str, Any]:
        try:
            r = await self._client.get(f"{self.base}{path}", timeout=5)
        except (httpx.TransportError, httpx.TimeoutException) as e:
            raise ProviderError(ErrorCategory.SERVER_ERROR,
                                f"ollama not reachable ({type(e).__name__})") from None
        r.raise_for_status()
        data: dict[str, Any] = r.json()
        return data

    async def installed(self) -> set[str]:
        return {m["name"] for m in (await self._get("/api/tags")).get("models", [])}

    async def loaded(self) -> set[str]:
        return {m["name"] for m in (await self._get("/api/ps")).get("models", [])}

    async def unload(self, model: str) -> None:
        await self._client.post(f"{self.base}/api/generate",
                                json={"model": model, "keep_alive": 0}, timeout=30)

    async def _make_room_for(self, alias: str, model: str) -> None:
        if not self.is_heavy(alias):
            return
        heavy_models = {self.aliases[a] for a in self.policy.on_demand_aliases
                        if self.aliases.get(a)}
        resident = (await self.loaded()) & (heavy_models - {model})
        excess = len(resident) + 1 - self.policy.max_heavy_models_loaded
        for m in sorted(resident)[:max(excess, 0)]:
            await self.unload(m)

    # --------------------------------------------------------------- health

    async def check_health(self) -> Health:
        try:
            names = await self.installed()
        except ProviderError as e:
            return self._set(HealthState.UNAVAILABLE, str(e))
        except httpx.HTTPError as e:
            return self._set(HealthState.DEGRADED, f"ollama error: {e}")
        default = self.aliases[self.policy.default_alias]
        if default not in names:
            return self._set(HealthState.DEGRADED, f"default model {default} not pulled yet")
        return self._set(HealthState.HEALTHY, f"{len(names)} model(s); default {default}")

    # ------------------------------------------------------------- complete

    async def _complete(self, request: ProviderRequest) -> ProviderResult:
        alias, model = self.model_for(request)
        await self._make_room_for(alias, model)
        messages = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        content = request.user_request
        if request.context:
            content = f"{request.context}\n\n---\n{request.user_request}"
        user: dict[str, Any] = {"role": "user", "content": content}
        if request.images:
            user["images"] = [base64.b64encode(img).decode() for img in request.images]
        messages.append(user)
        # Thinking per task type comes from models.yaml (benchmark-driven, plan §9A).
        # JSON replies may think too: Ollama keeps the thinking apart and applies the
        # format to the answer only (planner steps need it — Phase 11 drill).
        think = request.task_type in self.policy.think_task_types
        budget = request.limits.max_output_tokens + (1536 if think else 0)
        body: dict[str, Any] = {
            "model": model, "messages": messages, "stream": False, "think": think,
            "keep_alive": f"{self.policy.idle_unload_seconds}s",
            "options": {"num_predict": budget, "temperature": 0.3, "num_ctx": self.policy.num_ctx},
        }
        if request.json_output:
            body["format"] = "json"
        t0 = time.perf_counter()
        try:
            r = await self._client.post(f"{self.base}/api/chat", json=body,
                                        timeout=request.limits.timeout_seconds)
        except httpx.TimeoutException:
            raise ProviderError(ErrorCategory.TIMEOUT, f"{model}: timed out") from None
        except httpx.TransportError as e:
            self._set(HealthState.UNAVAILABLE, f"ollama not reachable ({type(e).__name__})")
            raise ProviderError(ErrorCategory.SERVER_ERROR,
                                f"ollama not reachable ({type(e).__name__})") from None
        if r.status_code == 404:
            raise ProviderError(ErrorCategory.MODEL_UNAVAILABLE,
                                f"model {model} ({alias}) is not installed")
        if r.status_code >= 500:
            raise ProviderError(ErrorCategory.SERVER_ERROR, f"ollama HTTP {r.status_code}")
        if r.status_code >= 400:
            raise ProviderError(ErrorCategory.BAD_REQUEST,
                                f"ollama HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        answer = str((data.get("message") or {}).get("content", "")).strip()
        return ProviderResult(
            ResultStatus.OK, self.name, model=model, answer=answer,
            usage=Usage(input_tokens=int(data.get("prompt_eval_count", 0)),
                        output_tokens=int(data.get("eval_count", 0)),
                        seconds=time.perf_counter() - t0),
            verification_hints=("empty answer",) if not answer else (),
        )
