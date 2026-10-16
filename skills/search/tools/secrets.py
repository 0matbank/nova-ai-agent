"""Look for leaked secrets under a folder (Phase 16, Security agent): files that are
secrets by name (.env, *.pem, id_rsa …) and lines that hold a key / token /
password — found with the same patterns the log redactor masks, so this matches
what Nova itself treats as a secret.

Read-only (`search`, GREEN). The result never contains a secret: only the file,
the line number and the line with the value masked. Nova's own secret folders
are never scanned (path policy)."""

from __future__ import annotations

import asyncio

from core.log import redact
from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult
from core.skills.scan import ScanBudget, walk

MAX_FILE_BYTES = 1_000_000
SECRET_FILES = ("credentials.json", "token.json", "id_rsa", "id_ed25519", "id_ecdsa")
SECRET_SUFFIXES = (".pem", ".key", ".pfx", ".p12")
BINARY_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".7z",
                   ".rar", ".gz", ".exe", ".dll", ".so", ".pyc", ".mp4", ".mp3", ".mkv",
                   ".woff", ".woff2", ".ttf", ".db", ".sqlite", ".bin", ".gguf")


class P(Params):
    root: str
    limit: int = 50


def precheck(p: P, env: ToolEnv) -> None:
    root = env.paths.resolve(p.root)
    env.paths.check_scan_root(root)
    if not root.is_dir():
        raise PolicyDenied(f"{root} is not a folder")


def _run(p: P, env: ToolEnv) -> ToolResult:
    root = env.paths.resolve(p.root)
    budget = ScanBudget()
    hits: list[dict[str, object]] = []
    scanned = 0
    for path in walk(root, env.paths, budget):
        name = path.name.lower()
        env_file = name == ".env" or (name.startswith(".env.") and name != ".env.example")
        if env_file or name in SECRET_FILES or name.endswith(SECRET_SUFFIXES):
            hits.append({"file": str(path), "line": None, "kind": "secret file"})
        elif not name.endswith(BINARY_SUFFIXES):
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            scanned += 1
            for n, line in enumerate(text.splitlines(), start=1):
                masked = redact(line)
                if masked != line:
                    hits.append({"file": str(path), "line": n, "kind": "secret value",
                                 "masked": masked.strip()[:160]})
                    if len(hits) >= p.limit:
                        break
        if len(hits) >= min(p.limit, 500):
            break
    note = " (scan budget reached — results may be incomplete)" if budget.exhausted else ""
    files = sorted({str(h["file"]) for h in hits})
    summary = (f"{len(hits)} possible secret(s) in {len(files)} file(s) under {root}: "
               + ", ".join(f"{h['file']}" + (f" line {h['line']}" if h["line"] else "")
                           for h in hits[:10])
               if hits else f"no secrets found in {scanned} text file(s) under {root}") + note
    return ToolResult(True, summary,
                      {"root": str(root), "findings": hits, "files_scanned": scanned,
                       "complete": not budget.exhausted},
                      {"findings": len(hits)}, untrusted=True)


async def run(p: P, env: ToolEnv) -> ToolResult:
    return await asyncio.to_thread(_run, p, env)   # disk-heavy: keep the loop free


TOOL = Tool(name="secrets", params=P, run=run, action="search",
            target=lambda p: p.root, precheck=precheck)
