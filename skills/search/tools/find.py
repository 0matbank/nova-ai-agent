from __future__ import annotations

import asyncio
import fnmatch

from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult
from core.skills.scan import ScanBudget, walk


class P(Params):
    root: str
    pattern: str = "*"
    name_contains: str | None = None
    include_dirs: bool = False
    limit: int = 100


def precheck(p: P, env: ToolEnv) -> None:
    root = env.paths.resolve(p.root)
    env.paths.check_scan_root(root)
    if not root.is_dir():
        raise PolicyDenied(f"{root} is not a folder")


def _run(p: P, env: ToolEnv) -> ToolResult:
    root = env.paths.resolve(p.root)
    budget = ScanBudget()
    needle = (p.name_contains or "").lower()
    hits = []
    for path in walk(root, env.paths, budget, include_dirs=p.include_dirs):
        name = path.name.lower()
        if fnmatch.fnmatch(name, p.pattern.lower()) and needle in name:
            hits.append(str(path))
            if len(hits) >= min(p.limit, 1000):
                break
    note = " (scan budget reached — results may be incomplete)" if budget.exhausted else ""
    return ToolResult(True, f"{len(hits)} match(es) under {root}{note}",
                      {"root": str(root), "matches": hits, "complete": not budget.exhausted},
                      untrusted=True)


async def run(p: P, env: ToolEnv) -> ToolResult:
    return await asyncio.to_thread(_run, p, env)   # disk-heavy: keep the loop free


TOOL = Tool(name="find", params=P, run=run, action="search",
            target=lambda p: p.root, precheck=precheck)
