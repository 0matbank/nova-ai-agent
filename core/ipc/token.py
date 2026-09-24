"""Internal RPC bearer token (plan §17A, §17D).

The token lives in <secrets_dir>/internal_rpc.token (user+SYSTEM ACL, never in
the repo, never logged). It is generated on first use and can be rotated at
any time: every process re-reads the file when its mtime changes, so core and
workers pick up a rotated token without restarting.
"""

from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path

TOKEN_FILE = "internal_rpc.token"  # noqa: S105 - a file name, not a secret
TOKEN_BYTES = 32


class TokenStore:
    def __init__(self, secrets_dir: Path) -> None:
        self.path = secrets_dir / TOKEN_FILE
        self._cached: str | None = None
        self._mtime: float | None = None

    def ensure(self) -> str:
        if not self.path.exists():
            self._write(secrets.token_urlsafe(TOKEN_BYTES))
        return self.current()

    def current(self) -> str:
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            return self.ensure()
        if self._cached is None or mtime != self._mtime:
            value = self.path.read_text(encoding="utf-8").strip()
            if len(value) < 32:
                raise ValueError(f"{self.path.name} is too short to be a valid token")
            self._cached, self._mtime = value, mtime
        return self._cached

    def rotate(self) -> str:
        self._write(secrets.token_urlsafe(TOKEN_BYTES))
        return self.current()

    def matches(self, presented: str) -> bool:
        return hmac.compare_digest(presented.encode(), self.current().encode())

    def _write(self, value: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(value, encoding="utf-8")
        os.replace(tmp, self.path)          # atomic: readers never see a half file
        self._cached = None
