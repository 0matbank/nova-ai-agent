"""Phase 0 verification: repository foundation + secrets boundary.

Stdlib only, so it can run before any dependency is installed.
Exit code 0 = PASS, 1 = FAIL.

    python scripts/verify_foundation.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parent

REPO_DIRS = [
    "core/orchestrator", "core/router", "core/planner", "core/executor",
    "core/verifier", "core/permissions", "core/queue",
    "channels/telegram", "channels/whatsapp", "channels/local",
    "agents/master", "agents/developer", "agents/windows", "agents/browser",
    "agents/research", "agents/devops", "agents/media", "agents/security",
    "agents/verifier", "agents/projects",
    "skills", "models/router",
    "workers/core-service", "workers/desktop-worker",
    "workers/browser-worker", "workers/privileged-broker",
    "memory", "tests", "installer", "scripts", "docs",
]

# Local-only runtime folders (plan §23, §24) — must live outside the repo.
RUNTIME_DIRS = [
    "data", "data/projects", "data/task-history", "data/indexes",
    "secrets", "sessions/browser", "sessions/whatsapp",
    "workspace", "downloads", "logs", "backups", "local-models",
]

# Paths that must be ignored by git if they ever appear inside the repo.
MUST_IGNORE = [
    ".env", ".env.local", "secrets/x.txt", "sessions/browser/state.json",
    "cookies.json", "storage_state.json", "data/agent.db", "agent.db",
    "logs/core/x.log", "downloads/a.zip", "workspace/a.txt",
    "local-models/m.gguf", "server.key", "cert.pem", "gh.token",
    "credentials/github.json", "client_secret_123.json",
    ".venv/pyvenv.cfg", "node_modules/x/index.js",
]

# Paths that must NOT be ignored (would silently drop source files).
MUST_TRACK = [
    ".env.example", "core/permissions/credentials_vault.py",
    "channels/telegram/bot.py", "memory/store.py", "docs/PLAN.md",
]

SECRET_PATTERNS = {
    "telegram_bot_token": re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    "anthropic_key": re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "github_token": re.compile(r"\b(ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}"),
    "private_key": re.compile(r"-----BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY-----"),
}


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=APP, capture_output=True, text=True)


def main() -> int:
    failures: list[str] = []

    for d in REPO_DIRS:
        if not (APP / d).is_dir():
            failures.append(f"missing repo dir: app/{d}")

    for d in RUNTIME_DIRS:
        p = ROOT / d
        if not p.is_dir():
            failures.append(f"missing runtime dir: {p}")
        elif APP in p.resolve().parents or p.resolve() == APP:
            failures.append(f"runtime dir inside repo: {p}")

    for f in [".gitignore", ".env.example"]:
        if not (APP / f).is_file():
            failures.append(f"missing file: app/{f}")

    if git("rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
        failures.append("app/ is not a git repository")
    else:
        ignored = set(git("check-ignore", "--no-index", *MUST_IGNORE).stdout.split())
        for p in MUST_IGNORE:
            if p not in ignored:
                failures.append(f"not ignored (secrets boundary leak): {p}")
        ignored = set(git("check-ignore", "--no-index", *MUST_TRACK).stdout.split())
        for p in MUST_TRACK:
            if p in ignored:
                failures.append(f"wrongly ignored source path: {p}")

        # Scan everything git would commit: tracked + untracked-not-ignored.
        files = git("ls-files", "--cached", "--others", "--exclude-standard").stdout.splitlines()
        for rel in files:
            path = APP / rel
            name = path.name.lower()
            if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
                failures.append(f"secret file would be committed: {rel}")
            if not path.is_file() or path.stat().st_size > 2_000_000:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for label, rx in SECRET_PATTERNS.items():
                if rx.search(text):
                    failures.append(f"possible {label} in {rel}")

    if failures:
        print("PHASE 0 VERIFY: FAIL")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PHASE 0 VERIFY: PASS")
    print(f"  repo dirs: {len(REPO_DIRS)}  runtime dirs: {len(RUNTIME_DIRS)}")
    print(f"  ignore rules checked: {len(MUST_IGNORE)}  source paths checked: {len(MUST_TRACK)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
