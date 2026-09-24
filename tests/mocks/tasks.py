"""Real TaskStore/TaskEngine/permission stack on a temp SQLite DB, with a
recording sender instead of Telegram."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

APP_DIR = Path(__file__).resolve().parents[2]


@dataclass
class TaskEnv:
    store: Any
    engine: Any
    commands: Any
    sent: list[tuple[str, str]]
    db_path: Path
    permissions: Any = None
    approvals: Any = None
    audit: Any = None
    security: Any = None
    buttons: list[list[list[tuple[str, str]]]] = field(default_factory=list)

    def last_callback(self, approve: bool) -> str:
        row = self.buttons[-1][0]
        return row[0][1] if approve else row[1][1]


def make_task_env(db_path: Path, backups: Path, approval_timeout: int = 300) -> TaskEnv:
    from core.config.schema import NotificationThrottle, PermissionsConfig, TaskEngineSection
    from core.db.engine import make_sessionmaker
    from core.db.migrate import check_and_migrate
    from core.notify.notifier import Notifier
    from core.orchestrator.security_commands import SecurityCommands
    from core.orchestrator.task_commands import TaskCommands
    from core.permissions.approvals import ApprovalManager
    from core.permissions.audit import AuditTrail
    from core.permissions.engine import PermissionEngine
    from core.queue.engine import TaskEngine
    from core.queue.store import TaskStore

    sent: list[tuple[str, str]] = []
    buttons: list[list[list[tuple[str, str]]]] = []

    async def send(chat_id: str, msg: Any) -> None:
        sent.append((chat_id, msg.text))
        if msg.buttons:
            buttons.append(msg.buttons)

    policy = NotificationThrottle(
        min_progress_interval_seconds=30, soft_max_progress_updates_per_task=5,
        repeated_error_cooldown_seconds=300, batch_small_results=True,
        exempt_message_types=["approval_required", "user_input_required",
                              "critical_security_alert", "task_completed",
                              "task_failed_permanently", "agent_going_offline",
                              "recovery_after_restart"])
    raw = yaml.safe_load((APP_DIR / "config" / "permissions.yaml").read_text("utf-8"))
    raw["approval"]["timeout_seconds"] = approval_timeout
    perm_cfg = PermissionsConfig.model_validate(raw)

    mig = check_and_migrate(db_path, backups)
    sessions = make_sessionmaker(mig.engine)
    notifier = Notifier(policy, send)
    store = TaskStore(sessions)
    audit = AuditTrail(sessions)
    permissions = PermissionEngine(perm_cfg, sessions)
    approvals = ApprovalManager(sessions, perm_cfg.approval, store, audit, notifier)
    engine = TaskEngine(store, notifier,
                        TaskEngineSection(max_retries=2, idle_poll_seconds=1, list_limit=10),
                        permissions, approvals)
    env = TaskEnv(store, engine, TaskCommands(store, engine, 10, "Asia/Dhaka"), sent, db_path,
                  permissions, approvals, audit,
                  SecurityCommands(permissions, approvals, store, audit))
    env.buttons = buttons
    return env
