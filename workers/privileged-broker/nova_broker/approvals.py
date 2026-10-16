"""The broker's own approval check (defence in depth).

Core already asks the owner and consumes the approval (APPROVED → USED) before a
RED skill call. The broker does not take core's word for it: it recomputes the
approval fingerprint from the exact request it is about to execute and looks for a
USED approval of the same task, action and fingerprint, decided by the owner within
the last few minutes, that the broker has not used before (its own single-use
ledger, kept in the protected state folder).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

LEDGER = "used_approvals.json"
KEEP = 2000


def fingerprint(action: str, target: str, details: dict[str, Any] | None) -> str:
    """Identical to core.permissions.approvals.fingerprint (a test pins this)."""
    blob = json.dumps({"a": action, "t": target, "d": details or {}}, sort_keys=True,
                      ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def _parse(ts: Any) -> datetime | None:
    if ts is None:
        return None
    try:
        dt = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


class ApprovalChecker:
    def __init__(self, db_path: Path, state_dir: Path, max_age_seconds: int = 900) -> None:
        self.db_path = db_path
        self.ledger = state_dir / LEDGER
        self.max_age = timedelta(seconds=max_age_seconds)

    def _used(self) -> list[int]:
        try:
            data = json.loads(self.ledger.read_text(encoding="utf-8"))
            return [int(x) for x in data]
        except (FileNotFoundError, ValueError, TypeError):
            return []

    def _remember(self, approval_id: int) -> None:
        ids = (self._used() + [approval_id])[-KEEP:]
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.ledger.with_suffix(".tmp")
        tmp.write_text(json.dumps(ids), encoding="utf-8")
        os.replace(tmp, self.ledger)

    def claim(self, task_id: str, action: str, target: str,
              details: dict[str, Any]) -> tuple[int | None, str]:
        """Find and claim the owner's approval for exactly this request.
        Returns (approval id, "") or (None, why not)."""
        if not task_id.isdigit():
            return None, "an admin action must belong to a task"
        if not self.db_path.is_file():
            return None, "approval database not found"
        fp = fingerprint(action, target, details)
        uri = self.db_path.resolve().as_uri() + "?mode=ro"
        try:
            con = sqlite3.connect(uri, uri=True, timeout=5)
        except sqlite3.Error as e:
            return None, f"approval database unreadable ({type(e).__name__})"
        try:
            rows = con.execute(
                "SELECT id, details, decided_at, decided_by FROM approvals "
                "WHERE task_id = ? AND action = ? AND status = 'USED' ORDER BY id DESC",
                (int(task_id), action)).fetchall()
        except sqlite3.Error as e:
            return None, f"approval database unreadable ({type(e).__name__})"
        finally:
            with contextlib.suppress(sqlite3.Error):
                con.close()
        used = set(self._used())
        now = datetime.now(UTC)
        reason = "no owner approval for this exact request"
        for approval_id, raw, decided_at, decided_by in rows:
            try:
                details_row = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except ValueError:
                continue
            if details_row.get("fingerprint") != fp:
                continue
            if not decided_by:
                reason = "the approval was not decided by the owner"
                continue
            when = _parse(decided_at)
            if when is None or now - when > self.max_age:
                reason = "the approval is too old — ask again"
                continue
            if int(approval_id) in used:
                reason = "this approval was already used"
                continue
            self._remember(int(approval_id))
            return int(approval_id), ""
        return None, reason
