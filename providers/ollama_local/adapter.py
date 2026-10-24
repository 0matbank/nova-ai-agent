"""Local Ollama provider (plan §8.2 "Local Ollama", §9A model policy).

- Models come from role aliases in models.yaml — never hard-coded here.
- On-demand heavy models (e.g. local_code_review) are loaded only when asked,
  and at most `max_heavy_models_loaded` are resident: others are unloaded first.
- Idle models unload after `idle_unload_seconds` (Ollama keep_alive).
- Talks only to the loopback Ollama API.
- Memory (owner 2026-10-16, RAM 93–100 %): the model runner (llama.cpp server) keeps
  a prompt cache in RAM that grows ~0.35 GB per new long prompt up to 8 GiB while the
  default model stays loaded. The cap is the Ollama server's LLAMA_ARG_CACHE_RAM
  (user env, set to 1024); as a guard, under critical RAM an idle, bloated runner is
  reloaded, which empties its cache. Warm-up uses the same options as a chat, so
  the first question does not load the model a second time.
"""

from __future__ import annotations

import asyncio
import base64
import time
from typing import Any

import httpx
import psutil

from core.config.schema import ModelsConfig
from core.log import get_logger
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

_log = get_logger("provider")
DEFAULT_URL = "http://127.0.0.1:11434"
# under critical RAM, an idle runner holding more than this is reloaded (cache emptied);
# a freshly loaded, fully GPU-offloaded qwen3:8b holds ~0.9 GB
RUNNER_RSS_LIMIT_GB = 3.0

# capability / task type → role alias (plan §9A)
TASK_ALIAS = {
    "simple": "fast_general", "offline": "fast_general", "fallback": "fast_general",
    "reasoning": "fast_general", "planning": "fast_general", "quick_answer": "fast_general",
    "fact_check": "fast_general", "current_info": "fast_general",
    "intent_classification": "intent_classification",
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
        self._heavy_inflight = 0                       # heavy requests running now
        self._inflight = 0                             # all requests running now
        # set after heavy use: only then does a default request check /api/ps (no extra
        # round-trip before every quick question)
        self._heavy_maybe_resident = False
        self._swap_back: asyncio.Task[None] | None = None

    async def aclose(self) -> None:
        self._cancel_swap_back()
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

    def keep_alive(self, alias: str) -> str | int:
        """The default model stays in GPU memory (owner 2026-10-15: no cold-start wait
        on a quick question); heavy on-demand models unload when idle."""
        if self.policy.keep_default_loaded and not self.is_heavy(alias):
            return -1
        return f"{self.policy.idle_unload_seconds}s"

    async def warm(self) -> bool:
        """Load the default model now (empty request = load only). With exclusive VRAM an
        idle heavy model (e.g. left over from before a restart) is unloaded first."""
        model = self.aliases[self.policy.default_alias]
        if self.policy.exclusive_vram:
            await self.relieve()
        try:
            r = await self._client.post(f"{self.base}/api/generate", timeout=180,
                                        json={"model": model, "keep_alive": -1
                                              if self.policy.keep_default_loaded
                                              else f"{self.policy.idle_unload_seconds}s",
                                              # same as every chat — otherwise Ollama
                                              # loads the model again on the first one
                                              "options": self._load_options()})
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    def _load_options(self) -> dict[str, Any]:
        """Options that decide how the runner is loaded; must match between warm-up and
        chat (a difference makes Ollama reload the whole model)."""
        return {"num_ctx": self.policy.num_ctx}

    @staticmethod
    def runner_rss_gb() -> float:
        """Resident memory of the model runner process(es) (llama.cpp server)."""
        total = 0
        for p in psutil.process_iter(["name"]):
            name = (p.info.get("name") or "").lower()
            if name.startswith(("llama-server", "ollama_llama_server")):
                try:
                    total += p.memory_info().rss
                except psutil.Error:
                    continue
        return total / 1024 ** 3

    async def flush_if_bloated(self, limit_gb: float = RUNNER_RSS_LIMIT_GB) -> bool:
        """Critical RAM guard: an idle runner over the limit (its prompt cache grew) is
        reloaded — a few seconds, no answer is lost because nothing is running."""
        if self._inflight or not self.policy.keep_default_loaded:
            return False
        rss = await asyncio.to_thread(self.runner_rss_gb)
        if rss <= limit_gb:
            return False
        model = str(self.aliases[self.policy.default_alias])
        try:
            await self.unload(model)
        except httpx.HTTPError:
            return False
        ok = await self.warm()
        _log.info(f"model runner held {rss:.1f} GB of RAM (prompt cache) — reloaded "
                  f"{model}" + ("" if ok else " (warm-up failed; loads on next use)"),
                  extra={"action": "resources.flush", "status": "ok" if ok else "failed"})
        return True

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

    def heavy_models(self) -> set[str]:
        return {str(self.aliases[a]) for a in self.policy.on_demand_aliases
                if self.aliases.get(a)}

    async def _make_room_for(self, alias: str, model: str) -> None:
        heavy_models = self.heavy_models()
        if not self.is_heavy(alias):
            if (self.policy.exclusive_vram and self._heavy_inflight == 0
                    and self._heavy_maybe_resident):
                # the default is needed again: an idle heavy model makes way now
                self._cancel_swap_back()
                await self.relieve()
            return
        self._heavy_maybe_resident = True
        self._cancel_swap_back()
        loaded = await self.loaded()
        resident = loaded & (heavy_models - {model})
        excess = len(resident) + 1 - self.policy.max_heavy_models_loaded
        for m in sorted(resident)[:max(excess, 0)]:
            await self.unload(m)
        default = str(self.aliases[self.policy.default_alias])
        if self.policy.exclusive_vram and default in loaded:
            await self.unload(default)              # one LLM in VRAM at a time

    def _cancel_swap_back(self) -> None:
        if self._swap_back is not None and not self._swap_back.done():
            self._swap_back.cancel()
        self._swap_back = None

    async def _swap_back_later(self) -> None:
        """Heavy work finished: after a quiet period the heavy model leaves VRAM and the
        default model returns (owner 2026-10-15: no two LLMs resident without need)."""
        await asyncio.sleep(self.policy.heavy_idle_seconds)
        if self._heavy_inflight:
            return
        await self.relieve()
        if self.policy.keep_default_loaded:
            await self.warm()

    async def relieve(self) -> list[str]:
        """Unload heavy models that are not working right now (plan §17H critical:
        unload_unused_ollama_models). Returns what was unloaded."""
        if self._heavy_inflight:
            return []
        try:
            idle = sorted((await self.loaded()) & self.heavy_models())
            for m in idle:
                await self.unload(m)
        except (ProviderError, httpx.HTTPError):
            return []
        self._heavy_maybe_resident = False
        if idle:
            _log.info(f"unloaded idle heavy model(s): {', '.join(idle)}",
                      extra={"action": "resources.unload", "status": "ok"})
        return idle

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
        heavy = self.is_heavy(alias)
        await self._make_room_for(alias, model)
        if heavy:
            self._heavy_inflight += 1
        self._inflight += 1
        try:
            return await self._chat(request, alias, model)
        finally:
            self._inflight -= 1
            if heavy:
                self._heavy_inflight -= 1
                if self.policy.exclusive_vram and self._heavy_inflight == 0:
                    self._cancel_swap_back()
                    self._swap_back = asyncio.create_task(self._swap_back_later())

    async def _chat(self, request: ProviderRequest, alias: str, model: str) -> ProviderResult:
        messages = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        parts = [p for p in (request.context, request.previous_attempt and
                             f"Previous attempt / current state:\n{request.previous_attempt}")
                 if p]
        content = "\n\n---\n".join([*parts, request.user_request])
        user: dict[str, Any] = {"role": "user", "content": content}
        if request.images:
            user["images"] = [base64.b64encode(img).decode() for img in request.images]
        messages.append(user)
        # Thinking per task type comes from models.yaml (benchmark-driven, plan §9A).
        # JSON replies may think too: Ollama keeps the thinking apart and applies the
        # format to the answer only (planner steps need it — Phase 11 drill).
        think = (request.task_type in self.policy.think_task_types if request.think is None
                 else request.think)
        budget = request.limits.max_output_tokens + (1536 if think else 0)
        body: dict[str, Any] = {
            "model": model, "messages": messages, "stream": False, "think": think,
            "keep_alive": self.keep_alive(alias),
            "options": {"num_predict": budget, "temperature": 0.3, **self._load_options()},
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
