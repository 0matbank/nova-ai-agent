"""Real TaskStore/TaskEngine on a temp SQLite DB, with a recording sender."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class TaskEnv:
    store: Any
    engine: Any
    commands: Any
    sent: list[tuple[str, str]]
    db_path: Path


def make_task_env(db_path: Path, backups: Path) -> TaskEnv:
    from core.config.schema import NotificationThrottle, TaskEngineSection
    from core.db.engine import make_sessionmaker
    from core.db.migrate import check_and_migrate
    from core.notify.notifier import Notifier
    from core.orchestrator.task_commands import TaskCommands
    from core.queue.engine import TaskEngine
    from core.queue.store import TaskStore

    sent: list[tuple[str, str]] = []

    async def send(chat_id: str, msg: Any) -> None:
        sent.append((chat_id, msg.text))

    policy = NotificationThrottle(
        min_progress_interval_seconds=30, soft_max_progress_updates_per_task=5,
        repeated_error_cooldown_seconds=300, batch_small_results=True,
        exempt_message_types=["approval_required", "user_input_required",
                              "critical_security_alert", "task_completed",
                              "task_failed_permanently", "agent_going_offline",
                              "recovery_after_restart"])
    mig = check_and_migrate(db_path, backups)
    store = TaskStore(make_sessionmaker(mig.engine))
    engine = TaskEngine(store, Notifier(policy, send),
                        TaskEngineSection(max_retries=2, idle_poll_seconds=1, list_limit=10))
    return TaskEnv(store, engine, TaskCommands(store, engine, 10, "Asia/Dhaka"), sent, db_path)
