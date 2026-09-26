"""The single gate every skill tool call passes through:

    validate params → policy precheck → action ∈ declared actions →
    idempotency ledger (side effects) → Permission Engine (ctx.require) →
    run → redact output → audit
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from core.log import get_logger, redact
from core.permissions.audit import AuditTrail
from core.queue.engine import TaskBlocked, TaskContext
from core.skills.api import ToolEnv, ToolResult
from core.skills.ledger import DONE, PENDING, SideEffectLedger, effect_key, is_side_effect
from core.skills.registry import SkillError, SkillRegistry

_log = get_logger("tasks")
AUDITED_PREFIXES = ("file.delete", "process.kill", "powershell.run", "terminal.run",
                    "message.send", "git.push", "admin.")


def _redact_value(v: Any) -> Any:
    if isinstance(v, str):
        return redact(v)
    if isinstance(v, dict):
        return {k: _redact_value(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_redact_value(x) for x in v]
    return v


class SkillRunner:
    def __init__(self, registry: SkillRegistry, env: ToolEnv,
                 audit: AuditTrail | None = None,
                 ledger: SideEffectLedger | None = None) -> None:
        self.registry = registry
        self.env = env
        self.audit = audit
        self.ledger = ledger

    async def invoke(self, ctx: TaskContext, skill: str, tool: str,
                     params: dict[str, Any]) -> ToolResult:
        sk, t = self.registry.get(skill, tool)
        try:
            p = t.params.model_validate(params)
        except ValidationError as e:
            raise SkillError(f"{skill}.{tool}: invalid params: {e.errors()}") from None
        env = ToolEnv(self.env.config, self.env.paths, self.env.workers, ctx.task.id)
        if t.precheck is not None:
            t.precheck(p, env)                     # PolicyDenied propagates
        action = await t.action_for(p, env)
        if action not in sk.declared.get(tool, []):
            raise SkillError(f"{skill}.{tool} requested undeclared action {action!r}")
        entry_id: int | None = None
        key = ""
        if self.ledger is not None and is_side_effect(action):
            key = effect_key(action, t.target(p), p.model_dump(mode="json"))
            done = self.ledger.lookup(ctx.task.id, key)
            if done is not None and done.status == DONE:
                # plan §9.1: a retry / another provider never repeats a side effect
                _log.info(f"{skill}.{tool}: already done in this task — not repeated",
                          extra={"task_id": ctx.task.id, "skill": skill, "action": action,
                                 "status": "idempotent_skip"})
                return ToolResult(True, f"already done earlier in this task (not repeated): "
                                        f"{done.summary}", {"idempotent_replay": True})
            if done is not None and done.status == PENDING:
                raise TaskBlocked(
                    f"{action} on {t.target(p)} was started earlier but its result was never "
                    "recorded (e.g. a crash mid-action). I won't run it again blindly — "
                    "please check whether it happened, then tell me to continue or cancel.")
        check = None
        if t.safety_check is not None:
            async def check() -> tuple[bool, str]:
                assert t.safety_check is not None
                return await t.safety_check(p, env)
        await ctx.require(action, target=t.target(p), summary=t.summary(p),
                          details=p.model_dump(mode="json"), safety_check=check)
        if key and self.ledger is not None:
            entry_id = self.ledger.begin(ctx.task.id, key, action, t.target(p))
        result = await t.run(p, env)
        if entry_id is not None and self.ledger is not None:
            self.ledger.finish(entry_id, result.ok, redact(result.summary))
        safe = ToolResult(result.ok, redact(result.summary), _redact_value(result.data),
                          _redact_value(result.evidence), result.untrusted)
        _log.info(f"{skill}.{tool} -> {'ok' if safe.ok else 'failed'}",
                  extra={"task_id": ctx.task.id, "skill": skill, "action": action,
                         "status": "ok" if safe.ok else "failed"})
        if self.audit is not None and action.startswith(AUDITED_PREFIXES):
            self.audit.record(actor=f"skill:{skill}", action=action,
                              status="ok" if safe.ok else "failed", target=t.target(p),
                              task_id=ctx.task.id, details={"summary": safe.summary})
        return safe
