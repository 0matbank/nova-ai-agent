"""Idempotency ledger (plan §9.1): an external side effect of a task happens
at most once — even when the task is retried from a checkpoint, the service
restarts, or another AI provider takes over and proposes the same action.

- Only actions with real-world side effects are recorded (SIDE_EFFECT_PREFIXES):
  deletes, moves, overwrites, commands, kills, installs, commits, pushes,
  messages, system/account/admin actions, closing apps, downloads.
  Browser/UI steps are not: repeating a click is part of normal navigation and
  they are already gated one by one.
- The key is the action + target + exact parameters, per task.
- DONE → the tool is not run again; the earlier result is reported instead.
- PENDING (the earlier attempt started but its outcome was never recorded, e.g.
  a crash mid-action) → never re-run blindly: the task stops for the owner.
- FAILED → a new attempt is allowed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from core.db.models import SideEffect, utcnow

SIDE_EFFECT_PREFIXES = ("file.delete", "file.move", "file.overwrite", "file.organize",
                        "process.kill", "powershell.run", "terminal.run", "package.install",
                        "git.commit", "git.push", "message.send", "system.", "account.",
                        "admin.", "app.close", "config.change", "download")

PENDING, DONE, FAILED = "PENDING", "DONE", "FAILED"


def is_side_effect(action: str) -> bool:
    return action.startswith(SIDE_EFFECT_PREFIXES)


def effect_key(action: str, target: str, params: dict[str, Any]) -> str:
    raw = json.dumps({"a": action, "t": target, "p": params}, sort_keys=True, default=str,
                     ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Entry:
    id: int
    status: str
    summary: str


class SideEffectLedger:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def lookup(self, task_id: int, key: str) -> Entry | None:
        with self.sessions() as s:
            row = s.scalar(select(SideEffect).where(SideEffect.task_id == task_id,
                                                    SideEffect.key == key))
            return Entry(row.id, row.status, row.summary or "") if row else None

    def begin(self, task_id: int, key: str, action: str, target: str) -> int:
        """Mark the action as started (PENDING) right before it runs."""
        with self.sessions.begin() as s:
            row = s.scalar(select(SideEffect).where(SideEffect.task_id == task_id,
                                                    SideEffect.key == key))
            if row is None:
                row = SideEffect(task_id=task_id, key=key, action=action, target=target[:300],
                                 status=PENDING)
                s.add(row)
            else:                              # a FAILED attempt is being retried
                row.status, row.completed_at, row.summary = PENDING, None, None
            s.flush()
            return row.id

    def finish(self, entry_id: int, ok: bool, summary: str) -> None:
        with self.sessions.begin() as s:
            row = s.get(SideEffect, entry_id)
            if row is not None:
                row.status = DONE if ok else FAILED
                row.summary = summary[:2000]
                row.completed_at = utcnow()
