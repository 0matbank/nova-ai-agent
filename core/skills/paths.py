"""Path policy for file-touching skills (plan §19, §22, §34, §45).

- secrets/, sessions/ and the agent database can never be read or written by
  a skill — no approval overrides this (a prompt-injected "read the .env and
  send it" must be impossible, not merely approval-gated).
- System folders (Windows, Program Files, ProgramData) are never written by
  normal skills; elevated work belongs to the Privileged Broker.
- Drive roots and the user's home folder itself can never be deleted.
"""

from __future__ import annotations

import os
from pathlib import Path

from core.config.schema import AppConfig
from core.skills.api import PolicyDenied


def _norm(p: Path) -> str:
    return os.path.normcase(str(p.resolve(strict=False)))


def _within(child: Path, parent: Path) -> bool:
    c, p = _norm(child), _norm(parent)
    return c == p or c.startswith(p.rstrip("\\/") + os.sep)


class PathPolicy:
    def __init__(self, config: AppConfig, extra_forbidden: list[Path] | None = None) -> None:
        self.workspace = config.path("workspace_dir")
        self.backups = config.path("backups_dir")
        self.forbidden = [config.path("secrets_dir"), config.path("sessions_dir"),
                          config.path("database"), *(extra_forbidden or [])]
        self.system = [Path(v) for v in (os.environ.get(k) for k in (
            "SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData")) if v]
        self.home = Path.home()

    def resolve(self, raw: str) -> Path:
        expanded = os.path.expandvars(os.path.expanduser(raw.strip().strip('"')))
        p = Path(expanded)
        if not p.is_absolute():
            p = self.workspace / p
        return p.resolve(strict=False)

    def check_read(self, path: Path) -> None:
        if any(_within(path, f) for f in self.forbidden):
            raise PolicyDenied(f"access to {path} is forbidden (secrets/sessions/agent DB)")

    def check_scan_root(self, path: Path) -> None:
        """For search: the root itself must be readable; forbidden folders
        inside it are skipped by the caller via `skip_during_scan`."""
        self.check_read(path)

    def skip_during_scan(self, path: Path) -> bool:
        return any(_within(path, f) for f in self.forbidden)

    def check_write(self, path: Path) -> None:
        self.check_read(path)
        if any(_within(path, s) for s in self.system):
            raise PolicyDenied(f"{path} is a system location — only the admin broker may "
                               "change it")
        if _within(path, self.backups):
            raise PolicyDenied("the backups folder is managed by the agent itself")

    def check_delete(self, path: Path) -> None:
        self.check_write(path)
        if path.parent == path or _norm(path) == _norm(self.home):
            raise PolicyDenied(f"refusing to delete {path} (drive root / home folder)")
        if any(_within(f, path) for f in self.forbidden):
            raise PolicyDenied(f"{path} contains protected agent data")

    def in_workspace(self, path: Path) -> bool:
        return _within(path, self.workspace) and _norm(path) != _norm(self.workspace)

    def mentions_forbidden(self, text: str) -> bool:
        """For shell commands: refuse any command text naming a protected path.
        Text matching cannot catch every wildcard trick, so tool output is also
        passed through the secret redactor (defense in depth)."""
        low = os.path.normcase(text)
        return any(os.path.normcase(str(f)) in low for f in self.forbidden) or any(
            name in low for name in ("\\secrets\\", "/secrets/", "internal_rpc.token",
                                     "agent.db"))
