"""Secrets live only in <secrets_dir>/.env (plan §17G, §22) — never in YAML,
memory, or logs. Values are wrapped in SecretStr so repr/str never leak them."""

from __future__ import annotations

from pathlib import Path

from dotenv import dotenv_values
from pydantic import SecretStr

ENV_FILE = ".env"


class Secrets:
    def __init__(self, values: dict[str, SecretStr]) -> None:
        self._values = values

    def get(self, key: str) -> SecretStr | None:
        return self._values.get(key)

    def require(self, key: str) -> SecretStr:
        value = self._values.get(key)
        if value is None:
            raise KeyError(f"required secret {key!r} is not set in {ENV_FILE}")
        return value

    def keys(self) -> list[str]:
        return sorted(self._values)

    def raw_values(self) -> list[str]:
        """Plain values — only for the log redactor."""
        return [v.get_secret_value() for v in self._values.values()]

    def __repr__(self) -> str:
        return f"Secrets(keys={self.keys()})"


def load_secrets(secrets_dir: Path) -> Secrets:
    path = secrets_dir / ENV_FILE
    if not path.is_file():
        return Secrets({})
    raw = dotenv_values(path)
    return Secrets({k: SecretStr(v) for k, v in raw.items() if v})
