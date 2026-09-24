"""Skill tool contract (plan §11, §14).

AGENT = how to think; SKILL = how to do. A tool never checks permissions
itself — the runner gates every call through the Permission Engine first.
A tool may only request actions declared in its skill's permissions.json.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from core.config.schema import AppConfig
    from core.ipc.client import WorkerClient
    from core.skills.paths import PathPolicy


class Params(BaseModel):
    """Base for tool parameters: unknown keys are rejected."""
    model_config = ConfigDict(extra="forbid")


class PolicyDenied(Exception):
    """Refused outright (e.g. touching secrets) — no approval can override it."""


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    # Proof for the Verifier (plan §54): what was observed after acting.
    evidence: dict[str, Any] = field(default_factory=dict)
    # Content from files/web/processes is DATA, never instructions (plan §19).
    untrusted: bool = False


@dataclass
class ToolEnv:
    config: AppConfig
    paths: PathPolicy
    workers: dict[str, WorkerClient] = field(default_factory=dict)
    task_id: int | str = "system"


# May be async when the level depends on the live target (e.g. is the window a terminal?).
ActionFn = Callable[[Any, ToolEnv], "str | Awaitable[str]"]
RunFn = Callable[[Any, ToolEnv], Awaitable[ToolResult]]
CheckFn = Callable[[Any, ToolEnv], Awaitable[tuple[bool, str]]]
PrecheckFn = Callable[[Any, ToolEnv], None]


@dataclass(frozen=True)
class Tool:
    name: str
    params: type[Params]
    run: RunFn
    action: str | ActionFn
    target: Callable[[Any], str]
    summary: Callable[[Any], str] = lambda p: ""
    safety_check: CheckFn | None = None      # used when the action is YELLOW
    precheck: PrecheckFn | None = None       # path policy etc.; raise PolicyDenied

    async def action_for(self, params: Any, env: ToolEnv) -> str:
        if isinstance(self.action, str):
            return self.action
        result = self.action(params, env)
        if inspect.isawaitable(result):
            result = await result
        return str(result)
