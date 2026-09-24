"""Structured JSON logging (plan §17E).

- One rotating file per category: <logs_dir>/<category>/<category>.log
- Standard fields: timestamp, task_id, worker, agent, skill, provider,
  action, status, duration, error_code
- Context fields are carried by contextvars (see `log_context`)
- Every line passes through the Redactor — on the final rendered text, so
  messages, extras and tracebacks are all covered.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import logging.handlers
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.log.redaction import Redactor

FIELDS = (
    "task_id", "worker", "agent", "skill", "provider",
    "action", "status", "duration", "error_code",
)
ROOT_LOGGER = "agent"

_context: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "log_context", default={}  # noqa: B039 - never mutated, always replaced
)
_redactor = Redactor()


@contextlib.contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    """Attach fields (task_id, agent, ...) to every log line inside the block."""
    unknown = set(fields) - set(FIELDS)
    if unknown:
        raise ValueError(f"unknown log context fields: {sorted(unknown)}")
    token = _context.set({**_context.get(), **fields})
    try:
        yield
    finally:
        _context.reset(token)


class JsonFormatter(logging.Formatter):
    def __init__(self, worker: str, redactor: Redactor) -> None:
        super().__init__()
        self.worker = worker
        self.redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "category": record.name.removeprefix(f"{ROOT_LOGGER}."),
            "worker": self.worker,
            "message": record.getMessage(),
        }
        ctx = _context.get()
        for f in FIELDS:
            value = getattr(record, f, None)
            if value is None:
                value = ctx.get(f)
            if value is not None:
                entry[f] = value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return self.redactor(json.dumps(entry, ensure_ascii=False, default=str))


def setup_logging(
    logs_dir: Path,
    categories: list[str],
    *,
    worker: str,
    level: str = "INFO",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 10,
    known_secrets: list[str] | None = None,
    console: bool = True,
) -> None:
    """Configure `agent.<category>` loggers. Safe to call again (replaces handlers)."""
    _redactor.add(known_secrets or [])
    formatter = JsonFormatter(worker, _redactor)
    # HTTP client libraries log request URLs at INFO; Telegram URLs contain the token.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    root = logging.getLogger(ROOT_LOGGER)
    root.setLevel(level)
    root.propagate = False
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()
    if console:
        ch = logging.StreamHandler(sys.stderr)
        ch.setFormatter(formatter)
        root.addHandler(ch)

    for cat in categories:
        folder = logs_dir / cat
        folder.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger(f"{ROOT_LOGGER}.{cat}")
        for h in list(logger.handlers):
            logger.removeHandler(h)
            h.close()
        fh = logging.handlers.RotatingFileHandler(
            folder / f"{cat}.log", maxBytes=max_bytes, backupCount=backup_count,
            encoding="utf-8", delay=True,
        )
        fh.setFormatter(formatter)
        logger.addHandler(fh)
        logger.setLevel(level)
        logger.propagate = True   # also reaches the console handler on `agent`


def get_logger(category: str) -> logging.Logger:
    return logging.getLogger(f"{ROOT_LOGGER}.{category}")


def add_known_secrets(values: list[str]) -> None:
    _redactor.add(values)


def shutdown_logging() -> None:
    """Flush + close every handler (used by graceful shutdown, plan §32A step 11)."""
    for name in list(logging.root.manager.loggerDict):
        if name == ROOT_LOGGER or name.startswith(f"{ROOT_LOGGER}."):
            logger = logging.getLogger(name)
            for h in list(logger.handlers):
                h.flush()
                h.close()
                logger.removeHandler(h)
