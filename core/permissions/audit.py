"""Audit trail (plan §17E): approvals, rejections, permission changes, admin
actions, external messages, git push, deletes. Written to both the database
(audit_log table) and logs/audit/audit.log. Details are redacted first."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from core.db.models import AuditLog
from core.log import get_logger, redact

_log = get_logger("audit")


class AuditTrail:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def record(self, *, actor: str, action: str, status: str, target: str | None = None,
               task_id: int | None = None, details: dict[str, Any] | None = None) -> None:
        safe_target = redact(target) if target else None
        safe_details = (json.loads(redact(json.dumps(details, ensure_ascii=False, default=str)))
                        if details else None)
        with self._sessions.begin() as s:
            s.add(AuditLog(actor=actor, action=action, status=status, target=safe_target,
                           task_id=task_id, details=safe_details))
        _log.info(f"{action} {status}" + (f" — {safe_target}" if safe_target else ""),
                  extra={"action": action, "status": status,
                         "task_id": task_id, "agent": actor})
