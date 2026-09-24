from __future__ import annotations

import asyncio
import time

from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult
from core.skills.fileops import iso
from core.skills.scan import ScanBudget, walk


class P(Params):
    root: str
    hours: float = 24
    limit: int = 50


def precheck(p: P, env: ToolEnv) -> None:
    root = env.paths.resolve(p.root)
    env.paths.check_scan_root(root)
    if not root.is_dir():
        raise PolicyDenied(f"{root} is not a folder")


def _run(p: P, env: ToolEnv) -> ToolResult:
    root = env.paths.resolve(p.root)
    cutoff = time.time() - p.hours * 3600
    budget = ScanBudget()
    found = []
    for path in walk(root, env.paths, budget):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime >= cutoff:
            found.append((mtime, path))
    found.sort(reverse=True)
    items = [{"path": str(pth), "modified": iso(m)} for m, pth in found[: min(p.limit, 500)]]
    return ToolResult(True, f"{len(found)} file(s) changed in the last {p.hours:g}h under {root}",
                      {"root": str(root), "files": items, "complete": not budget.exhausted},
                      untrusted=True)


async def run(p: P, env: ToolEnv) -> ToolResult:
    return await asyncio.to_thread(_run, p, env)   # disk-heavy: keep the loop free


TOOL = Tool(name="recent", params=P, run=run, action="search",
            target=lambda p: p.root, precheck=precheck)
