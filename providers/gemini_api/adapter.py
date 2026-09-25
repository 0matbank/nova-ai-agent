"""Google Gemini API provider (plan §8.2 "Gemini API") — text generation.

- Models come from the `gemini_api` alias set in models.yaml, never hard-coded.
  Each task type may name a backup model (`<task>_backup`): an overloaded or
  missing model switches to it first (plan §8.7 "alternative model, then next
  provider"), then the router moves on to the next provider (local Ollama).
- A 429 (quota / rate limit) puts the adapter in COOLDOWN; once it has passed,
  health is HEALTHY again and the router uses Gemini as primary again.
- The key is GEMINI_API_KEY from secrets/.env; it is sent only as a header.
Pulled forward from Phase 14 at the owner's request (2026-09-25): only the
task types given a priority in providers.yaml are routed here.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from pydantic import SecretStr

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

BASE = "https://generativelanguage.googleapis.com/v1beta/models"
COOLDOWN_SECONDS = 600
OVERLOADED = {500, 502, 503, 504}


class GeminiAdapter(ProviderAdapter):
    name = "gemini_api"
    capabilities = frozenset({"summarization", "reasoning", "bangla"})

    def __init__(self, api_key: SecretStr | None, aliases: dict[str, str | None],
                 client: httpx.AsyncClient | None = None) -> None:
        super().__init__()
        self.api_key = api_key
        self.aliases = aliases
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10))
        self.cooldown_until = 0.0

    async def aclose(self) -> None:
        await self._client.aclose()

    def models_for(self, task_type: str) -> list[str]:
        names = [self.aliases.get(task_type) or self.aliases.get("default"),
                 self.aliases.get(f"{task_type}_backup")]
        return [n for n in names if n]

    async def check_health(self) -> Health:
        if self.api_key is None:
            return self._set(HealthState.UNAVAILABLE, "GEMINI_API_KEY not set")
        left = self.cooldown_until - time.time()
        if left > 0:
            return self._set(HealthState.COOLDOWN, f"quota/rate limit — retry in {int(left)}s")
        return self._set(HealthState.HEALTHY, "key present")

    async def _complete(self, request: ProviderRequest) -> ProviderResult:
        if self.api_key is None:
            raise ProviderError(ErrorCategory.AUTH, "GEMINI_API_KEY not set")
        models = self.models_for(request.task_type)
        if not models:
            raise ProviderError(ErrorCategory.MODEL_UNAVAILABLE,
                                f"no gemini model configured for {request.task_type!r}")
        last: ProviderError | None = None
        for model in models:
            try:
                return await self._call(model, request)
            except ProviderError as e:
                if e.category not in (ErrorCategory.MODEL_UNAVAILABLE,
                                      ErrorCategory.SERVER_ERROR):
                    raise
                last = e                           # overloaded/missing → backup model
        assert last is not None
        raise last

    async def _call(self, model: str, request: ProviderRequest) -> ProviderResult:
        assert self.api_key is not None
        text = request.user_request
        if request.context:
            text = f"{request.context}\n\n---\n{request.user_request}"
        gen: dict[str, Any] = {
            # thinking tokens count against this, so leave room beyond the answer
            "maxOutputTokens": request.limits.max_output_tokens + 1536,
            "temperature": 0.3,
            "thinkingConfig": {"thinkingLevel": "low"},
        }
        if request.json_output:
            gen["responseMimeType"] = "application/json"
        body: dict[str, Any] = {"contents": [{"role": "user", "parts": [{"text": text}]}],
                                "generationConfig": gen}
        if request.system:
            body["systemInstruction"] = {"parts": [{"text": request.system}]}
        t0 = time.perf_counter()
        try:
            r = await self._client.post(
                f"{BASE}/{model}:generateContent", json=body,
                headers={"x-goog-api-key": self.api_key.get_secret_value()},
                timeout=request.limits.timeout_seconds)
        except httpx.TimeoutException:
            raise ProviderError(ErrorCategory.TIMEOUT, f"{model}: timed out") from None
        except httpx.TransportError as e:
            raise ProviderError(ErrorCategory.SERVER_ERROR,
                                f"gemini not reachable ({type(e).__name__})") from None
        if r.status_code == 429:
            self.cooldown_until = time.time() + COOLDOWN_SECONDS
            self._set(HealthState.COOLDOWN, "quota/rate limit (429)")
            raise ProviderError(ErrorCategory.RATE_LIMIT, f"{model}: rate limited (429)",
                                retry_after=COOLDOWN_SECONDS)
        if r.status_code in (401, 403):
            raise ProviderError(ErrorCategory.AUTH, f"{model}: auth error {r.status_code}")
        if r.status_code == 404:
            raise ProviderError(ErrorCategory.MODEL_UNAVAILABLE, f"{model}: not found")
        if r.status_code in OVERLOADED:
            raise ProviderError(ErrorCategory.SERVER_ERROR, f"{model}: HTTP {r.status_code}")
        if r.status_code >= 400:
            raise ProviderError(ErrorCategory.BAD_REQUEST,
                                f"{model}: HTTP {r.status_code} {r.text[:200]}")
        data = r.json()
        cand = (data.get("candidates") or [{}])[0]
        answer = "".join(str(p.get("text", "")) for p in (cand.get("content") or {})
                         .get("parts", []) if not p.get("thought")).strip()
        usage = data.get("usageMetadata") or {}
        if not answer:
            raise ProviderError(ErrorCategory.SERVER_ERROR,
                                f"{model}: empty answer ({cand.get('finishReason')})")
        return ProviderResult(
            ResultStatus.OK, self.name, model=model, answer=answer,
            usage=Usage(input_tokens=int(usage.get("promptTokenCount", 0)),
                        output_tokens=int(usage.get("candidatesTokenCount", 0)),
                        seconds=time.perf_counter() - t0))
