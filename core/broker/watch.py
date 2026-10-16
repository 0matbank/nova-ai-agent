"""Broker health in core (Phase 22): /status line, start-if-needed, CRITICAL alert after
repeated failures (plan §17F), and /lockdown passed on to the broker."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Any

from core.broker.client import BrokerClient, BrokerError, BrokerUnavailable
from core.log import get_logger

_log = get_logger("core")
Alert = Callable[[str], Awaitable[None]]
FAILS_BEFORE_ALERT = 3


class BrokerWatch:
    def __init__(self, client: BrokerClient, every: float = 60.0) -> None:
        self.client = client
        self.every = every
        self.state: dict[str, Any] | None = None
        self.fails = 0
        self.alerted = False

    def probe(self) -> tuple[bool, str]:
        if not self.client.enabled:
            return True, "off (workers.yaml privileged_broker.enabled)"
        if not self.client.installed:
            return False, "not installed — scripts/broker_install.py"
        st = self.state
        if st is None:
            return False, "not answering"
        if not st.get("elevated"):
            return False, "running WITHOUT administrator rights"
        return True, (f"ready (v{st.get('version')}, administrator"
                      + (", LOCKDOWN" if st.get("lockdown") else "") + ")")

    async def check_once(self, alert: Alert | None = None) -> None:
        if not self.client.enabled or not self.client.installed:
            self.state = None
            return
        try:
            self.state = await self.client.ensure_running()
        except BrokerError as e:
            self.state = None
            _log.error(f"broker status refused: {e}", extra={"action": "broker.health",
                                                             "status": "error"})
        if self.state is not None:
            self.fails, self.alerted = 0, False
            return
        self.fails += 1
        _log.warning(f"broker not answering ({self.fails}x)",
                     extra={"action": "broker.health", "status": "down"})
        if self.fails >= FAILS_BEFORE_ALERT and not self.alerted and alert is not None:
            self.alerted = True
            await alert("🚨 CRITICAL: Admin broker বারবার সাড়া দিচ্ছে না — admin কাজ "
                        "(service/firewall/install/restart) এখন হবে না। PC-তে "
                        "`uv run python scripts/broker_install.py --check` চালিয়ে দেখুন।")

    async def run(self, stop: asyncio.Event, alert: Alert | None = None) -> None:
        while not stop.is_set():
            await self.check_once(alert)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self.every)

    async def lockdown(self, on: bool) -> str:
        if not self.client.enabled or not self.client.installed:
            return "Privileged broker: install করা নেই — admin কাজ এমনিতেই বন্ধ"
        try:
            await self.client.lockdown(on)
        except (BrokerUnavailable, BrokerError) as e:
            return f"Privileged broker: সাড়া দেয়নি ({e}) — core-এ সব admin কাজ বন্ধ আছে"
        await self.check_once()
        return ("Privileged broker: বন্ধ (lockdown) — শুধু restart cancel চলবে" if on
                else "Privileged broker: আবার চালু")
