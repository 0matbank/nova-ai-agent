"""Resource pressure (plan §17H Resource Manager): RAM / VRAM level from the
thresholds in resources.yaml, refreshed in the background. The provider router
reads it ("critical → route_heavy_local_to_cloud"), and a change into critical
alerts the owner once ("notify_user")."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import psutil

from core.config.schema import ResourcesConfig
from core.health import pc_status
from core.log import get_logger

_log = get_logger("core")
LEVELS = ("normal", "moderate", "high", "critical")


def level_for(cfg: ResourcesConfig, ram_percent: float, vram_percent: float | None) -> str:
    ram, vram = cfg.ram_percent, cfg.vram_percent
    if ram_percent >= ram.critical or (vram_percent is not None and vram_percent >= vram.critical):
        return "critical"
    if ram_percent >= ram.high or (vram_percent is not None and vram_percent >= vram.high):
        return "high"
    if ram_percent >= ram.moderate:
        return "moderate"
    return "normal"


# Owner 2026-10-16: RAM hovering at 92↔94 % sent a new critical alert every
# minute. Leaving critical needs this many points below the threshold, and the
# owner hears about it at most once per ALERT_COOLDOWN_SECONDS.
HYSTERESIS_POINTS = 5.0
ALERT_COOLDOWN_SECONDS = 30 * 60


def top_memory(n: int = 3) -> str:
    """Who holds the RAM: biggest programs by resident memory, same names summed."""
    by_name: dict[str, int] = {}
    for p in psutil.process_iter(["name", "memory_info"]):
        mem = p.info.get("memory_info")
        if mem is not None and p.info.get("name"):
            by_name[p.info["name"]] = by_name.get(p.info["name"], 0) + mem.rss
    top = sorted(by_name.items(), key=lambda kv: kv[1], reverse=True)[:n]
    return ", ".join(f"{name} {rss / 1024 ** 3:.1f} GB" for name, rss in top)


class ResourcePressure:
    def __init__(self, cfg: ResourcesConfig, disk_path: Path) -> None:
        self.cfg = cfg
        self.disk_path = disk_path
        self.level = "normal"
        self.detail = ""
        self._last_alert = float("-inf")
        # plan §17H critical actions (e.g. unload_unused_ollama_models)
        self.relievers: list[Callable[[], Awaitable[object]]] = []

    def __call__(self) -> str:
        return self.level

    async def refresh(self) -> str:
        s = await pc_status(self.disk_path)
        vram = max((g.mem_used_mb / g.mem_total_mb * 100 for g in s.gpus if g.mem_total_mb),
                   default=None)
        new = level_for(self.cfg, s.ram_percent, vram)
        if self.level == "critical" and new != "critical":
            # stay critical until it is clearly better, not 1 % below the line
            ram, vr = self.cfg.ram_percent, self.cfg.vram_percent
            if s.ram_percent > ram.critical - HYSTERESIS_POINTS or (
                    vram is not None and vram > vr.critical - HYSTERESIS_POINTS):
                new = "critical"
        self.detail = f"RAM {s.ram_percent:.0f}%" + (f", VRAM {vram:.0f}%" if vram else "")
        if new != self.level:
            if new == "critical":
                now = time.monotonic()
                top = await asyncio.to_thread(top_memory)
                msg = (f"PC memory is critical ({self.detail}) — heavy local AI work goes to "
                       f"the cloud until it recovers. Biggest users: {top}")
                if now - self._last_alert >= ALERT_COOLDOWN_SECONDS:
                    self._last_alert = now
                    _log.critical(msg, extra={"action": "resources.pressure", "status": new})
                else:                    # logged, but the owner's phone stays quiet
                    _log.warning(msg, extra={"action": "resources.pressure", "status": new})
            else:
                _log.info(f"resource pressure {self.level} → {new} ({self.detail})",
                          extra={"action": "resources.pressure", "status": new})
        self.level = new
        if new == "critical":
            for relieve in self.relievers:
                try:
                    await relieve()
                except Exception:  # noqa: BLE001 - relief is best effort
                    _log.exception("resource relief failed",
                                   extra={"action": "resources.relieve"})
        return new

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.refresh()
            except Exception:  # noqa: BLE001 - monitoring must never stop the service
                _log.exception("resource check failed", extra={"action": "resources.pressure"})
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self.cfg.monitor_interval_seconds)
