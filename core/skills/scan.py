"""Bounded directory walk shared by search tools."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path

from core.skills.paths import PathPolicy

SKIP_DIR_NAMES = {"$recycle.bin", "system volume information", "node_modules", ".git",
                  "__pycache__", ".venv"}


class ScanBudget:
    def __init__(self, max_visits: int = 200_000, seconds: float = 20.0) -> None:
        self.max_visits = max_visits
        self.deadline = time.monotonic() + seconds
        self.visits = 0
        self.exhausted = False

    def tick(self) -> bool:
        self.visits += 1
        if self.visits > self.max_visits or time.monotonic() > self.deadline:
            self.exhausted = True
        return not self.exhausted


def walk(root: Path, policy: PathPolicy, budget: ScanBudget,
         include_dirs: bool = False) -> Iterator[Path]:
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                entries = list(it)
        except OSError:
            continue
        for e in entries:
            if not budget.tick():
                return
            p = Path(e.path)
            if policy.skip_during_scan(p):
                continue
            try:
                is_dir = e.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                if e.name.lower() not in SKIP_DIR_NAMES:
                    stack.append(p)
                if include_dirs:
                    yield p
            else:
                yield p
