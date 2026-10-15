"""Git plumbing shared by the git skill and the coding flow (plan §35, Phase 15).

- Only folders of projects registered in config/projects; never Nova's own
  app / data / secrets folders.
- Credentials: git's own credential manager on this PC — Nova never sees or
  stores a token or password.
- Nothing destructive: pull is fast-forward only, push never forces, and only
  branches named `nova/...` are pushed (owner choice 2026-10-15: every task on
  its own branch, merging stays with the owner).
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

from core.config.schema import AppConfig, ProjectProfile
from core.log import redact
from core.skills.api import PolicyDenied

BRANCH_PREFIX = "nova/"
GIT = shutil.which("git") or "git"
NO_PROMPT = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}     # never hang on a login prompt
MAX_SCAN_BYTES = 2_000_000


def forbidden_roots(cfg: AppConfig) -> list[Path]:
    """Folders an AI or a git action may never touch: Nova itself and its private data."""
    return [cfg.app_dir, *(cfg.path(k) for k in ("data_dir", "secrets_dir", "sessions_dir",
                                                  "logs_dir", "backups_dir"))]


def project_folder(cfg: AppConfig, key: str) -> tuple[ProjectProfile, Path]:
    project = cfg.projects.get(key)
    if project is None:
        raise PolicyDenied(f"unknown project {key!r} (registered: {', '.join(cfg.projects)})")
    if not project.local_folder:
        raise PolicyDenied(f"project {key!r} has no local_folder in config/projects")
    folder = Path(project.local_folder).resolve()
    if any(folder == r.resolve() or r.resolve() in folder.parents for r in forbidden_roots(cfg)):
        raise PolicyDenied(f"{folder} is one of Nova's own folders — never changed by a task")
    if not (folder / ".git").exists():
        raise PolicyDenied(f"{folder} is not a git repository")
    return project, folder


async def git(folder: Path, *args: str, timeout: float = 120) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        GIT, "-C", str(folder), *args, stdin=asyncio.subprocess.DEVNULL, env=NO_PROMPT,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, f"git {' '.join(args[:2])} timed out after {timeout:.0f}s"
    return proc.returncode or 0, out.decode("utf-8", "replace")


async def current_branch(folder: Path) -> str:
    code, out = await git(folder, "rev-parse", "--abbrev-ref", "HEAD")
    return out.strip() if code == 0 else ""


async def head(folder: Path) -> str:
    code, out = await git(folder, "rev-parse", "HEAD")
    return out.strip() if code == 0 else ""


async def remote_url(folder: Path, name: str = "origin") -> str | None:
    code, out = await git(folder, "remote", "get-url", name)
    return out.strip() if code == 0 and out.strip() else None


async def changed_files(folder: Path) -> list[str]:
    _, status = await git(folder, "status", "--porcelain")
    return [ln[3:].strip() for ln in status.splitlines()
            if ln.strip() and "__pycache__" not in ln]


async def secret_findings(folder: Path) -> list[str]:
    """Owner rule: no secret, token or .env is ever committed. Scans the pending change
    (tracked diff + new files) with the same patterns as the log redactor."""
    hits: list[str] = []
    _, diff = await git(folder, "diff", "HEAD")
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++") and redact(line) != line:
            hits.append("changed lines")
            break
    _, untracked = await git(folder, "ls-files", "--others", "--exclude-standard")
    for rel in untracked.splitlines():
        rel = rel.strip()
        if not rel or "__pycache__" in rel:
            continue
        name = Path(rel).name.lower()
        if name == ".env" or name.endswith((".pem", ".key", ".pfx", ".p12")) or \
                name in ("credentials.json", "token.json", "id_rsa", "id_ed25519"):
            hits.append(f"{rel} (secret file)")
            continue
        path = folder / rel
        try:
            if path.stat().st_size > MAX_SCAN_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if redact(text) != text:
            hits.append(rel)
    return hits
