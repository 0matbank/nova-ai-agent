"""Top processes by memory or CPU."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Literal

import psutil

from core.skills.api import Params, Tool, ToolEnv, ToolResult


class P(Params):
    sort_by: Literal["memory", "cpu"] = "memory"
    name_contains: str | None = None
    limit: int = 15


def _collect(p: P) -> list[dict[str, object]]:
    procs = list(psutil.process_iter(["pid", "name", "memory_info", "username"]))
    if p.sort_by == "cpu":
        for pr in procs:
            with contextlib.suppress(psutil.Error):
                pr.cpu_percent(None)
        psutil.cpu_percent(0.5)
    rows = []
    for pr in procs:
        try:
            name = pr.info["name"] or ""
            if p.name_contains and p.name_contains.lower() not in name.lower():
                continue
            mem = pr.info["memory_info"].rss if pr.info["memory_info"] else 0
            cpu = pr.cpu_percent(None) if p.sort_by == "cpu" else None
            rows.append({"pid": pr.info["pid"], "name": name, "memory_mb": round(mem / 2**20, 1),
                         "cpu_percent": cpu})
        except psutil.Error:
            continue
    key = "cpu_percent" if p.sort_by == "cpu" else "memory_mb"
    rows.sort(key=lambda r: r[key] or 0, reverse=True)  # type: ignore[arg-type,return-value]
    return rows[: min(p.limit, 100)]


async def run(p: P, env: ToolEnv) -> ToolResult:
    rows = await asyncio.to_thread(_collect, p)
    top = ", ".join(f"{r['name']} ({r['memory_mb']} MB)" for r in rows[:5])
    return ToolResult(True, f"{len(rows)} process(es); top: {top}", {"processes": rows})


TOOL = Tool(name="processes", params=P, run=run, action="process.list",
            target=lambda p: p.name_contains or "all")
