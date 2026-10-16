"""The broker's policy: what it may do and where things are.

Installed as policy.json next to the protected Python (admin-only writable), built
from config/broker.yaml at install time. Changing it means reinstalling — with the
owner's UAC consent — so user-level code can never widen the allowlist.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Policy:
    pipe_name: str
    protocol_version: int
    token_file: Path                    # the internal RPC token (core's secrets folder)
    db_path: Path                       # Nova's approval database, opened read-only
    state_dir: Path                     # used approvals, lockdown flag (protected)
    log_dir: Path
    services: dict[str, str] = field(default_factory=dict)        # lower name → name
    tasks: dict[str, list[str]] = field(default_factory=dict)      # name → fixed argv
    winget: str = ""                    # absolute path resolved at install, "" = off
    approval_max_age_seconds: int = 900
    require_elevation: bool = True

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Policy:
        return cls(
            pipe_name=str(d["pipe_name"]),
            protocol_version=int(d["protocol_version"]),
            token_file=Path(d["token_file"]),
            db_path=Path(d["db_path"]),
            state_dir=Path(d["state_dir"]),
            log_dir=Path(d["log_dir"]),
            services={str(s).lower(): str(s) for s in d.get("services", [])},
            tasks={str(k): [str(a) for a in v] for k, v in d.get("tasks", {}).items()},
            winget=str(d.get("winget", "")),
            approval_max_age_seconds=int(d.get("approval_max_age_seconds", 900)),
            require_elevation=bool(d.get("require_elevation", True)),
        )

    @classmethod
    def load(cls, path: Path) -> Policy:
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def summary(self) -> dict[str, Any]:
        return {"services": sorted(self.services.values()), "tasks": sorted(self.tasks),
                "packages": bool(self.winget)}
