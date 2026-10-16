"""Shared run() for the admin skill's tools: send exactly the approved parameters to
the broker and turn its answer into a ToolResult with the broker's own evidence."""

from __future__ import annotations

from typing import Any

from core.broker.client import BrokerError, BrokerUnavailable, client_for
from core.skills.api import Params, ToolEnv, ToolResult

NOT_RUNNING = ("Admin broker চলছে না। একবার install করতে হবে: "
               "`uv run python scripts/broker_install.py` (UAC-তে Yes) — docs/BROKER.md")


async def run_on_broker(op: str, p: Params, target: str, env: ToolEnv) -> ToolResult:
    params: dict[str, Any] = p.model_dump(mode="json")
    client = client_for(env.config)
    try:
        result = await client.execute(op, params, target, env.task_id)
    except BrokerUnavailable as e:
        return ToolResult(False, f"{NOT_RUNNING} ({e})", {"broker": "unavailable"})
    except BrokerError as e:
        return ToolResult(False, f"Broker: {e.bangla()}", {"broker_error": e.code})
    evidence = dict(result.get("evidence") or {})
    evidence["broker_verified"] = True
    if result.get("approval_id") is not None:
        evidence["approval_id"] = result["approval_id"]
    return ToolResult(True, str(result.get("summary", "done")), dict(evidence), evidence)
