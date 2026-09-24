"""Provider test doubles: a scriptable adapter and a fake Ollama HTTP API."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx

from providers.provider_base import (
    ErrorCategory,
    Health,
    HealthState,
    ProviderAdapter,
    ProviderError,
    ProviderRequest,
    ProviderResult,
    ResultStatus,
)


class FakeAdapter(ProviderAdapter):
    """`script` items: str (answer) | ErrorCategory (raise) | callable(request)->str."""

    def __init__(self, name: str, script: list[Any] | None = None,
                 state: HealthState = HealthState.HEALTHY,
                 capabilities: frozenset[str] = frozenset({"simple", "reasoning", "coding",
                                                           "review", "intent_classification",
                                                           "fallback", "offline"})) -> None:
        super().__init__()
        self.name = name
        self.capabilities = capabilities
        self.script = list(script or [])
        self.state = state
        self.requests: list[ProviderRequest] = []

    async def check_health(self) -> Health:
        return self._set(self.state, f"fake {self.state}")

    async def _complete(self, request: ProviderRequest) -> ProviderResult:
        self.requests.append(request)
        item = self.script.pop(0) if self.script else "ok"
        if isinstance(item, ErrorCategory):
            raise ProviderError(item, f"{self.name}: scripted {item}")
        answer = item(request) if callable(item) else str(item)
        return ProviderResult(ResultStatus.OK, self.name, model="fake-model", answer=answer)


class FakeOllama:
    """Minimal Ollama REST API over httpx.MockTransport."""

    def __init__(self, installed: list[str] | None = None,
                 reply: Callable[[dict[str, Any]], str] | str = "নমস্কার") -> None:
        self.installed = installed if installed is not None else ["qwen3:8b"]
        self.loaded: list[str] = []
        self.reply = reply
        self.chats: list[dict[str, Any]] = []
        self.unloaded: list[str] = []
        self.fail: list[Any] = []            # scripted: int status | "timeout" | "down"

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if self.fail:
            f = self.fail.pop(0)
            if f == "timeout":
                raise httpx.ReadTimeout("slow", request=request)
            if f == "down":
                raise httpx.ConnectError("refused", request=request)
            return httpx.Response(int(f), json={"error": "scripted"})
        path = request.url.path
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": n} for n in self.installed]})
        if path == "/api/ps":
            return httpx.Response(200, json={"models": [{"name": n} for n in self.loaded]})
        body = json.loads(request.content or b"{}")
        if path == "/api/generate" and body.get("keep_alive") == 0:
            self.unloaded.append(body["model"])
            self.loaded = [m for m in self.loaded if m != body["model"]]
            return httpx.Response(200, json={"done": True})
        if path == "/api/chat":
            self.chats.append(body)
            if body["model"] not in self.installed:
                return httpx.Response(404, json={"error": f"model '{body['model']}' not found"})
            if body["model"] not in self.loaded:
                self.loaded.append(body["model"])
            text = self.reply(body) if callable(self.reply) else self.reply
            return httpx.Response(200, json={"message": {"role": "assistant", "content": text},
                                             "done": True, "prompt_eval_count": 11,
                                             "eval_count": 7})
        return httpx.Response(404, json={"error": "no route"})
