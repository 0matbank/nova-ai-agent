"""Helpers shared by file-touching tools."""

from __future__ import annotations

import hashlib
import shutil
from datetime import UTC, datetime
from pathlib import Path

ENCODINGS = ("utf-8-sig", "utf-16", "cp1252")
MAX_HASH_BYTES = 512 * 1024 * 1024


def backup_file(path: Path, backups_dir: Path) -> Path:
    """Copy `path` into backups/file-backups/<timestamp>/ before it is changed."""
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    dst_dir = backups_dir / "file-backups" / stamp
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / path.name
    if path.is_dir():
        shutil.copytree(path, dst)
    else:
        shutil.copy2(path, dst)
    return dst


def read_text(path: Path, max_chars: int) -> tuple[str, str, bool]:
    raw = path.read_bytes()
    if b"\x00" in raw[:4096] and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise ValueError("binary file — not readable as text")
    for enc in ENCODINGS:
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text, enc = raw.decode("utf-8", errors="replace"), "utf-8 (lossy)"
    return text[:max_chars], enc, len(text) > max_chars


def sha256(path: Path) -> str | None:
    if path.stat().st_size > MAX_HASH_BYTES:
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat()
