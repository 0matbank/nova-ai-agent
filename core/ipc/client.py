"""Core-side client for loopback worker RPC (plan §17A, errors per §17F)."""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from core.ipc.protocol import HEADER_PROTOCOL, HEADER_REQUEST_ID, HEADER_TASK_ID, SYSTEM_TASK
from core.ipc.token import TokenStore


class WorkerError(Exception):
    pass


class WorkerUnavailable(WorkerError):
    """TRANSIENT: worker not running / not reachable / timed out."""


class WorkerAuthError(WorkerError):
    """CRITICAL: token rejected even after re-reading a rotated token."""


class WorkerProtocolMismatch(WorkerError):
    """Worker and core versions differ — update mismatch."""


class WorkerDesktopLocked(WorkerError):
    """Desktop Worker is up but the PC is locked (HTTP 423)."""


class WorkerClient:
    def __init__(self, name: str, host: str, port: int, tokens: TokenStore,
                 protocol_version: int, client: httpx.AsyncClient | None = None,
                 timeout: float = 10.0) -> None:
        self.name = name
        self.base = f"http://{host}:{port}"
        self.tokens = tokens
        self.protocol_version = protocol_version
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self, task_id: int | str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.tokens.current()}",
            HEADER_REQUEST_ID: uuid.uuid4().hex,
            HEADER_TASK_ID: str(task_id),
            HEADER_PROTOCOL: str(self.protocol_version),
        }

    async def call(self, method: str, path: str, *, task_id: int | str = SYSTEM_TASK,
                   json: dict[str, Any] | None = None) -> dict[str, Any]:
        for attempt in range(2):
            try:
                r = await self._client.request(method, f"{self.base}{path}",
                                               headers=self._headers(task_id), json=json)
            except (httpx.TransportError, httpx.TimeoutException) as e:
                raise WorkerUnavailable(f"{self.name}: {type(e).__name__}") from None
            if r.status_code == 401 and attempt == 0:
                continue           # token may have just been rotated; headers re-read it
            if r.status_code == 401:
                raise WorkerAuthError(f"{self.name}: token rejected")
            if r.status_code == 409:
                raise WorkerProtocolMismatch(f"{self.name}: {r.json().get('detail')}")
            if r.status_code == 423:
                raise WorkerDesktopLocked(f"{self.name}: desktop locked")
            if r.status_code >= 400:
                raise WorkerError(f"{self.name}: HTTP {r.status_code} {r.text[:200]}")
            data: dict[str, Any] = r.json()
            return data
        raise WorkerAuthError(f"{self.name}: token rejected")   # pragma: no cover

    async def health(self) -> dict[str, Any]:
        return await self.call("GET", "/v1/health")
