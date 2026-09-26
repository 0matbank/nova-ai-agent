"""The Antigravity CLI's own settings that Nova relies on (owner choice
2026-09-26: "same rules as Codex"). Written once by
scripts/antigravity_setup.py, with a backup of any existing file; the owner's
other settings are kept.

Verified live with agy 1.2.11 on Windows (2026-09-26):
- toolPermission "request-review": print mode refuses every terminal command.
  agy's terminal sandbox is still a preview on Windows and needs an admin
  escalation for each command, so "proceed-in-sandbox" cannot work here —
  the agent edits files with its file tools and Nova runs the tests itself.
- no file access outside the workspace;
- useG1Credits off: when the AI Pro quota is used up, never spend paid Google
  One AI credits — Nova moves on to the next provider instead;
- read_url / execute_url denied: the agent cannot fetch or drive any web page
  (its built-in Google search tool is not covered by permissions);
- git commands that change history or talk to a remote are denied outright
  (commit/push happen only through Nova with the owner, Phase 15).
Values are JSON booleans: agy rejects the whole file ("invalid value") and
silently falls back to its defaults when a value has the wrong type. The three
values below are agy's own defaults, and agy drops keys equal to a default when
it rewrites the file — so a missing key counts as set.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

REQUIRED: dict[str, str | bool] = {
    "toolPermission": "request-review",
    "allowNonWorkspaceAccess": False,
    "useG1Credits": False,
}
DENY = [f"command(git {verb})" for verb in (
    "commit", "push", "pull", "fetch", "reset", "rebase", "merge", "checkout", "switch",
    "restore", "stash", "clean", "tag", "branch", "remote", "config", "am", "cherry-pick",
    "revert", "filter-branch", "update-ref", "gc", "worktree", "submodule")] + [
    "read_url(*)", "execute_url(*)"]


def settings_path() -> Path:
    return Path(os.path.expanduser("~")) / ".gemini" / "antigravity-cli" / "settings.json"


def missing(data: dict[str, Any]) -> list[str]:
    """What still differs from Nova's rules (empty = all set)."""
    out = [f"{k}={v}" for k, v in REQUIRED.items() if data.get(k, v) != v]
    deny = set((data.get("permissions") or {}).get("deny") or [])
    out += [d for d in DENY if d not in deny]
    return out


def read(path: Path | None = None) -> dict[str, Any]:
    path = path or settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def ensure(path: Path | None = None) -> tuple[bool, Path | None]:
    """Merge Nova's rules into the settings file. Returns (changed, backup)."""
    path = path or settings_path()
    data = read(path)
    if not missing(data):
        return False, None
    backup = None
    if path.exists():
        backup = path.with_name(f"settings.json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(path, backup)
    data.update(REQUIRED)
    perms = data.setdefault("permissions", {})
    deny = list(perms.get("deny") or [])
    perms["deny"] = deny + [d for d in DENY if d not in deny]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return True, backup
