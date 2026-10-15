"""Resource pressure (plan §17H Resource Manager): RAM / VRAM level from the
thresholds in resources.yaml, refreshed in the background. The provider router
reads it ("critical → route_heavy_local_to_cloud"), and a change into critical
alerts the owner once ("notify_user")."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from pathlib import Path

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


class ResourcePressure:
    def __init__(self, cfg: ResourcesConfig, disk_path: Path) -> None:
        self.cfg = cfg
        self.disk_path = disk_path
        self.level = "normal"
        self.detail = ""
        # plan §17H critical actions (e.g. unload_unused_ollama_models)
        self.relievers: list[Callable[[], Awaitable[object]]] = []

    def __call__(self) -> str:
        return self.level

    async def refresh(self) -> str:
        s = await pc_status(self.disk_path)
        vram = max((g.mem_used_mb / g.mem_total_mb * 100 for g in s.gpus if g.mem_total_mb),
                   default=None)
        new = level_for(self.cfg, s.ram_percent, vram)
        self.detail = f"RAM {s.ram_percent:.0f}%" + (f", VRAM {vram:.0f}%" if vram else "")
        if new != self.level:
            if new == "critical":
                _log.critical(f"PC memory is critical ({self.detail}) — heavy local AI work "
                              "goes to the cloud until it recovers",
                              extra={"action": "resources.pressure", "status": new})
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
