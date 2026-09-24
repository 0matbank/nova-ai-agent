"""Approval button handling + /lockdown (plan §18, §27)."""

from __future__ import annotations

from channels.base import CallbackReply, IncomingCallback, IncomingMessage
from core.permissions.approvals import ApprovalManager, DecisionOutcome, parse_callback
from core.permissions.audit import AuditTrail
from core.permissions.engine import PermissionEngine
from core.queue.store import TaskStore

_DECISION_TEXT: dict[DecisionOutcome, tuple[str, str | None]] = {
    DecisionOutcome.APPROVED: ("✅ Approved", "✅ APPROVED — task আবার চলবে (এই approval একবারই)"),
    DecisionOutcome.REJECTED: ("❌ Rejected", "❌ REJECTED — task বাতিল"),
    DecisionOutcome.EXPIRED: ("⌛ সময় শেষ", "⌛ EXPIRED — task বাতিল"),
    DecisionOutcome.ALREADY_DECIDED: ("এটার সিদ্ধান্ত আগেই হয়ে গেছে", None),
    DecisionOutcome.NOT_FOUND: ("Approval পাওয়া যায়নি", None),
}


class SecurityCommands:
    def __init__(self, permissions: PermissionEngine, approvals: ApprovalManager,
                 store: TaskStore, audit: AuditTrail) -> None:
        self.permissions = permissions
        self.approvals = approvals
        self.store = store
        self.audit = audit

    async def on_button(self, cb: IncomingCallback) -> CallbackReply | None:
        parsed = parse_callback(cb.data)
        if parsed is None:
            return CallbackReply("Unknown button")
        nonce, approve = parsed
        outcome = self.approvals.decide(nonce, approve, decided_by=cb.user_id)
        toast, suffix = _DECISION_TEXT[outcome]
        return CallbackReply(toast, f"{cb.message_text}\n\n{suffix}" if suffix else None)

    async def lockdown(self, msg: IncomingMessage, args: list[str]) -> str:
        if args and args[0].lower() == "off":
            self.permissions.set_lockdown(False)
            self.audit.record(actor=msg.user_id, action="permission.lockdown", status="off")
            return ("🔓 Lockdown বন্ধ।\nQueue এখনো paused — নিশ্চিত হলে /resume দিন।")
        self.permissions.set_lockdown(True)
        self.store.pause_queue()
        rejected = self.approvals.reject_all_pending(actor="lockdown")
        self.audit.record(actor=msg.user_id, action="permission.lockdown", status="on",
                          details={"rejected_approvals": [a.id for a in rejected]})
        lines = [
            "🚨 LOCKDOWN চালু",
            "• নতুন task নেওয়া বন্ধ, queue paused",
            f"• Pending dangerous action বাতিল: {len(rejected)}",
            "• GREEN ছাড়া সব action blocked",
            "• Privileged broker: এখনো তৈরি হয়নি (Phase 22) — তাই চালু নেই",
            "• Telegram control চালু আছে",
            "\nবন্ধ করতে: /lockdown off",
        ]
        return "\n".join(lines)
