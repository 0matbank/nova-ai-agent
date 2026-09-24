"""Secret redaction (plan §17D, §17E: "Secrets কোনো log-এ print হবে না")."""

from __future__ import annotations

import re
from collections.abc import Iterable

MASK = "***REDACTED***"

PATTERNS = [
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),                     # Telegram bot token
    re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}"),                   # OpenAI / Anthropic
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),                          # Google API key
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}"),  # GitHub
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),             # Authorization header
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
]

MIN_KNOWN_SECRET_LEN = 6


class Redactor:
    def __init__(self, known_secrets: Iterable[str] = ()) -> None:
        self._known: list[str] = []
        self.add(known_secrets)

    def add(self, secrets: Iterable[str]) -> None:
        for s in secrets:
            if s and len(s) >= MIN_KNOWN_SECRET_LEN and s not in self._known:
                self._known.append(s)
        # Longest first so a secret containing another is fully masked.
        self._known.sort(key=len, reverse=True)

    def __call__(self, text: str) -> str:
        for s in self._known:
            if s in text:
                text = text.replace(s, MASK)
        for rx in PATTERNS:
            text = rx.sub(MASK, text)
        return text
