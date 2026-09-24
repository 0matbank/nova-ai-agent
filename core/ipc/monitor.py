"""Periodic worker health check (plan §31). Results feed /status probes.
A protocol mismatch or rejected token is alerted once (CRITICAL → Telegram)."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass

from core.ipc.client import (
    WorkerAuthError,
    WorkerClient,
    WorkerError,
    WorkerProtocolMismatch,
    WorkerUnavailable,
)
from core.log import get_logger

_log = get_logger("core")


@dataclass
class WorkerState:
    ok: bool = False
    detail: str = "not checked yet"
    checked_at: float | None = None
    alerted: bool = False


class WorkerMonitor:
    def __init__(self, clients: list[WorkerClient], interval: float = 30.0) -> None:
        self.clients = clients
        self.interval = interval
        self.state: dict[str, WorkerState] = {c.name: WorkerState() for c in clients}

    async def check(self, client: WorkerClient) -> None:
        st = self.state[client.name]
        critical = False
        try:
            h = await client.health()
            st.ok, st.detail = True, f"ok (v{h.get('protocol_version')}, pid {h.get('pid')})"
            st.alerted = False
        except WorkerUnavailable:
            st.ok, st.detail = False, "not running"
        except WorkerProtocolMismatch as e:
            st.ok, st.detail, critical = False, f"PROTOCOL MISMATCH — {e}", True
        except WorkerAuthError:
            st.ok, st.detail, critical = False, "token rejected", True
        except WorkerError as e:
            st.ok, st.detail = False, f"error: {e}"
        st.checked_at = time.time()
        if critical and not st.alerted:
            st.alerted = True
            _log.critical(f"{client.name} worker: {st.detail}",
                          extra={"action": "worker.health", "status": "critical"})

    async def check_all(self) -> None:
        await asyncio.gather(*(self.check(c) for c in self.clients))

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await self.check_all()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self.interval)

    def probe(self, name: str) -> Callable[[], tuple[bool, str]]:
        def _probe() -> tuple[bool, str]:
            st = self.state[name]
            return st.ok, st.detail
        return _probe

    async def aclose(self) -> None:
        for c in self.clients:
            await c.aclose()
