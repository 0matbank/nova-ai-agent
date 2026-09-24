"""Approvals (plan §18, §17D).

- An approval is bound to one task + one action fingerprint (action, target,
  details). Approving "delete a.txt" can never authorise "delete C:\\".
- Single use: APPROVED → USED atomically when the action runs.
- Short expiry: PENDING requests expire; an APPROVED-but-unused approval also
  expires `timeout_seconds` after the decision.
- Only an authenticated owner tapping the Telegram button can decide. Content
  (web pages, files, vision output) has no path to this code (plan §19, §57),
  and the recovery engine never approves (§53).
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from core.config.schema import ApprovalSection, Level
from core.db.models import Approval, utcnow
from core.notify.notifier import MessageType, Notifier
from core.permissions.audit import AuditTrail
from core.queue.states import TaskState
from core.queue.store import TaskStore

CALLBACK_PREFIX = "ap"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    USED = "USED"


class DecisionOutcome(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    ALREADY_DECIDED = "already_decided"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class ApprovalView:
    id: int
    task_id: int | None
    action: str
    level: str
    status: ApprovalStatus
    nonce: str
    target: str
    summary: str
    fingerprint: str


def fingerprint(action: str, target: str, details: dict[str, Any] | None) -> str:
    blob = json.dumps({"a": action, "t": target, "d": details or {}}, sort_keys=True,
                      ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def _view(a: Approval) -> ApprovalView:
    d = a.details or {}
    return ApprovalView(a.id, a.task_id, a.action, a.level, ApprovalStatus(a.status), a.nonce,
                        str(d.get("target", "")), str(d.get("summary", "")),
                        str(d.get("fingerprint", "")))


def callback_data(nonce: str, approve: bool) -> str:
    return f"{CALLBACK_PREFIX}:{nonce}:{'y' if approve else 'n'}"


def parse_callback(data: str) -> tuple[str, bool] | None:
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != CALLBACK_PREFIX or parts[2] not in ("y", "n"):
        return None
    return parts[1], parts[2] == "y"


class ApprovalManager:
    def __init__(self, sessions: sessionmaker[Session], settings: ApprovalSection,
                 store: TaskStore, audit: AuditTrail, notifier: Notifier | None) -> None:
        self._sessions = sessions
        self.settings = settings
        self.store = store
        self.audit = audit
        self.notifier = notifier
        self.on_task_released: list[Any] = []   # callbacks (e.g. engine.wake)

    @property
    def _ttl(self) -> timedelta:
        return timedelta(seconds=self.settings.timeout_seconds)

    # -------------------------------------------------------------- request

    def find(self, task_id: int, fp: str, status: ApprovalStatus) -> ApprovalView | None:
        with self._sessions() as s:
            rows = s.scalars(select(Approval).where(Approval.task_id == task_id,
                                                    Approval.status == status))
            for a in rows:
                if (a.details or {}).get("fingerprint") == fp and a.expires_at > utcnow():
                    return _view(a)
        return None

    async def request(self, *, task_id: int, chat_id: str, action: str, level: Level,
                      target: str, summary: str,
                      details: dict[str, Any] | None = None) -> ApprovalView:
        fp = fingerprint(action, target, details)
        existing = self.find(task_id, fp, ApprovalStatus.PENDING)
        if existing is not None:          # e.g. re-run after restart: don't spam
            return existing
        nonce = secrets.token_urlsafe(16)
        with self._sessions.begin() as s:
            a = Approval(task_id=task_id, action=action, level=level, nonce=nonce,
                         status=ApprovalStatus.PENDING, expires_at=utcnow() + self._ttl,
                         details={"target": target, "summary": summary,
                                  "fingerprint": fp, "details": details or {}})
            s.add(a)
            s.flush()
            view = _view(a)
        self.audit.record(actor="system", action="approval.request", status="pending",
                          target=f"{action} {target}", task_id=task_id,
                          details={"approval_id": view.id, "level": level})
        if self.notifier is not None:
            minutes = max(1, self.settings.timeout_seconds // 60)
            text = (f"🔐 APPROVAL REQUIRED — Task #{task_id}\n"
                    f"Action: {action} ({level})\n"
                    f"Target: {target}\n"
                    f"{summary}\n\n"
                    f"⏱️ {minutes} মিনিটের মধ্যে সিদ্ধান্ত না দিলে বাতিল হবে।")
            await self.notifier.notify(chat_id, MessageType.APPROVAL_REQUIRED, text,
                                       task_id=task_id, buttons=[[
                                           ("✅ Approve", callback_data(nonce, True)),
                                           ("❌ Reject", callback_data(nonce, False)),
                                       ]])
        return view

    # --------------------------------------------------------------- decide

    def decide(self, nonce: str, approve: bool, decided_by: str) -> DecisionOutcome:
        now = utcnow()
        with self._sessions.begin() as s:
            a = s.scalars(select(Approval).where(Approval.nonce == nonce)).first()
            if a is None:
                outcome, view = DecisionOutcome.NOT_FOUND, None
            elif a.status != ApprovalStatus.PENDING:
                outcome, view = DecisionOutcome.ALREADY_DECIDED, _view(a)
            elif a.expires_at <= now:
                a.status = ApprovalStatus.EXPIRED
                outcome, view = DecisionOutcome.EXPIRED, _view(a)
            else:
                a.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
                a.decided_at = now
                a.decided_by = decided_by
                if approve:              # fresh, short use-by window after approval
                    a.expires_at = now + self._ttl
                outcome = DecisionOutcome.APPROVED if approve else DecisionOutcome.REJECTED
                view = _view(a)

        if view is None:
            self.audit.record(actor=decided_by, action="approval.decide", status="not_found")
            return outcome
        self.audit.record(actor=decided_by, action=f"approval.{outcome.value}",
                          status=outcome.value, target=f"{view.action} {view.target}",
                          task_id=view.task_id, details={"approval_id": view.id})
        if view.task_id is not None and outcome in (DecisionOutcome.APPROVED,
                                                    DecisionOutcome.REJECTED,
                                                    DecisionOutcome.EXPIRED):
            self._release_task(view.task_id, outcome)
        return outcome

    def _release_task(self, task_id: int, outcome: DecisionOutcome) -> None:
        task = self.store.get(task_id)
        if task is None or task.state is not TaskState.WAITING_APPROVAL:
            return
        if outcome is DecisionOutcome.APPROVED:
            self.store.transition(task_id, TaskState.RETRYING, error_code=None)
        elif outcome is DecisionOutcome.REJECTED:
            self.store.transition(task_id, TaskState.CANCELLED, error_code="REJECTED_BY_USER")
        else:
            self.store.transition(task_id, TaskState.CANCELLED, error_code="APPROVAL_EXPIRED")
        for cb in self.on_task_released:
            cb()

    # -------------------------------------------------------------- consume

    def consume(self, approval_id: int) -> bool:
        """APPROVED → USED exactly once. False if already used or expired."""
        with self._sessions.begin() as s:
            res = s.execute(
                update(Approval)
                .where(Approval.id == approval_id, Approval.status == ApprovalStatus.APPROVED,
                       Approval.expires_at > utcnow())
                .values(status=ApprovalStatus.USED)
            )
            ok = res.rowcount == 1  # type: ignore[attr-defined]
        return bool(ok)

    # --------------------------------------------------------------- expiry

    async def expire_due(self) -> list[ApprovalView]:
        now = utcnow()
        with self._sessions.begin() as s:
            rows = list(s.scalars(select(Approval).where(
                Approval.status.in_([ApprovalStatus.PENDING, ApprovalStatus.APPROVED]),
                Approval.expires_at <= now)))
            for a in rows:
                a.status = ApprovalStatus.EXPIRED
            expired = [_view(a) for a in rows]
        for v in expired:
            self.audit.record(actor="system", action="approval.expired", status="expired",
                              target=f"{v.action} {v.target}", task_id=v.task_id,
                              details={"approval_id": v.id})
            if v.task_id is None:
                continue
            task = self.store.get(v.task_id)
            if task is not None and task.state is TaskState.WAITING_APPROVAL:
                self._release_task(v.task_id, DecisionOutcome.EXPIRED)
                if self.notifier is not None:
                    await self.notifier.notify(
                        task.chat_id, MessageType.USER_INPUT_REQUIRED,
                        f"⌛ Task #{v.task_id}: approval-এর সময় শেষ ({v.action}) — task বাতিল।",
                        task_id=v.task_id)
        return expired

    def pending(self) -> list[ApprovalView]:
        with self._sessions() as s:
            rows = s.scalars(select(Approval).where(Approval.status == ApprovalStatus.PENDING))
            return [_view(a) for a in rows]

    def reject_all_pending(self, actor: str) -> list[ApprovalView]:
        """Used by /lockdown: every pending dangerous action is cancelled."""
        rejected = []
        for v in self.pending():
            if self.decide(v.nonce, False, actor) is DecisionOutcome.REJECTED:
                rejected.append(v)
        return rejected
