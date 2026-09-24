from __future__ import annotations

import asyncio
from collections import defaultdict
from pathlib import Path

from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult
from core.skills.fileops import sha256
from core.skills.scan import ScanBudget, walk


class P(Params):
    root: str
    min_size: int = 1
    limit: int = 50


def precheck(p: P, env: ToolEnv) -> None:
    root = env.paths.resolve(p.root)
    env.paths.check_scan_root(root)
    if not root.is_dir():
        raise PolicyDenied(f"{root} is not a folder")


def _run(p: P, env: ToolEnv) -> ToolResult:
    root = env.paths.resolve(p.root)
    budget = ScanBudget()
    by_size: dict[int, list[Path]] = defaultdict(list)
    for path in walk(root, env.paths, budget):
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size >= p.min_size:
            by_size[size].append(path)
    groups = []
    for size, paths in sorted(by_size.items(), reverse=True):
        if len(paths) < 2:
            continue
        by_hash: dict[str, list[str]] = defaultdict(list)
        for f in paths:
            h = sha256(f)
            if h:
                by_hash[h].append(str(f))
        groups += [{"size": size, "sha256": h, "files": fs}
                   for h, fs in by_hash.items() if len(fs) > 1]
        if len(groups) >= p.limit:
            break
    wasted = sum(g["size"] * (len(g["files"]) - 1) for g in groups)
    return ToolResult(True, f"{len(groups)} duplicate group(s), {wasted} bytes reclaimable",
                      {"root": str(root), "groups": groups[: p.limit],
                       "complete": not budget.exhausted}, untrusted=True)


async def run(p: P, env: ToolEnv) -> ToolResult:
    return await asyncio.to_thread(_run, p, env)   # disk-heavy: keep the loop free


TOOL = Tool(name="duplicates", params=P, run=run, action="search",
            target=lambda p: p.root, precheck=precheck)
