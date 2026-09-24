"""Real SkillRegistry + SkillRunner + permission stack on a temp runtime root."""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.config import load_config
from core.config.schema import AppConfig
from core.queue.engine import ApprovalPending, TaskContext
from core.skills.api import ToolEnv, ToolResult
from core.skills.paths import PathPolicy
from core.skills.registry import SkillRegistry
from core.skills.runner import SkillRunner
from tests.mocks.tasks import TaskEnv, make_task_env

APP_DIR = Path(__file__).resolve().parents[2]
RUNTIME = ("data", "secrets", "sessions", "workspace", "downloads", "logs", "backups")


@dataclass
class SkillEnv:
    cfg: AppConfig
    tasks: TaskEnv
    runner: SkillRunner
    root: Path

    @property
    def workspace(self) -> Path:
        return self.cfg.path("workspace_dir")

    def call(self, skill: str, tool: str, params: dict[str, Any],
             task_id: int | None = None) -> ToolResult:
        if task_id is None:
            task_id = self.new_task()
        task = self.tasks.store.get(task_id)
        ctx = TaskContext(task, self.tasks.store, self.tasks.engine.notifier,
                          self.tasks.permissions, self.tasks.approvals)
        return asyncio.run(self.runner.invoke(ctx, skill, tool, params))

    def new_task(self) -> int:
        return int(self.tasks.store.create(title="t", request_text="t", task_type="skill",
                                           channel="telegram", chat_id="555").id)

    def call_with_approval(self, skill: str, tool: str, params: dict[str, Any],
                           approve: bool = True) -> tuple[ToolResult | None, int]:
        """First call must stop at approval; then decide and call again."""
        tid = self.new_task()
        try:
            self.call(skill, tool, params, tid)
        except ApprovalPending:
            pass
        else:
            raise AssertionError(f"{skill}.{tool} did not require approval")
        nonce = self.tasks.last_callback(approve).split(":")[1]
        self.tasks.approvals.decide(nonce, approve, "555")
        if not approve:
            return None, tid
        return self.call(skill, tool, params, tid), tid


def make_skill_env(tmp_path: Path, workers: dict[str, Any] | None = None) -> SkillEnv:
    cfg_dir = tmp_path / "config"
    if not cfg_dir.exists():
        shutil.copytree(APP_DIR / "config", cfg_dir)
    root = tmp_path / "rt"
    for d in RUNTIME:
        (root / d).mkdir(parents=True, exist_ok=True)
    cfg = load_config(cfg_dir, root)
    tasks = make_task_env(tmp_path / "db" / "agent.db", tmp_path / "db-bk")
    registry = SkillRegistry.load(cfg.skills)
    env = ToolEnv(cfg, PathPolicy(cfg), workers or {})
    return SkillEnv(cfg, tasks, SkillRunner(registry, env, tasks.audit), root)
