"""Windows Firewall: list Nova's rules (GREEN), add/remove a "Nova AI - …" rule (RED).
Nova only ever touches rules it named itself."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from core.broker.skill import run_on_broker
from core.skills.api import Params, Tool, ToolEnv, ToolResult


class P(Params):
    op: Literal["list", "add", "remove"] = "list"
    name: str = Field("", pattern=r"^([A-Za-z0-9][A-Za-z0-9 _.-]{0,59})?$",
                      description="rule label; Nova prefixes it with 'Nova AI - '")
    direction: Literal["in", "out"] = "in"
    rule_action: Literal["block", "allow"] = "block"
    protocol: Literal["TCP", "UDP"] = "TCP"
    port: str = Field("", pattern=r"^(\d{1,5}(-\d{1,5})?)?$",
                      description="a port or a range like 8000-8100 (needed for add)")


def action(p: P, env: ToolEnv) -> str:
    return "admin.status" if p.op == "list" else "admin.firewall"


def _target(p: P) -> str:
    return "firewall" if p.op == "list" else f"firewall rule 'Nova AI - {p.name}'"


def _summary(p: P) -> str:
    if p.op == "add":
        return (f"Firewall rule যোগ: 'Nova AI - {p.name}' — {p.rule_action} "
                f"{'incoming' if p.direction == 'in' else 'outgoing'} {p.protocol} port {p.port}")
    return f"Firewall rule মুছে ফেলা: 'Nova AI - {p.name}'"


async def run(p: P, env: ToolEnv) -> ToolResult:
    if p.op != "list" and not p.name:
        return ToolResult(False, "rule-এর একটা নাম লাগবে")
    if p.op == "add" and not p.port:
        return ToolResult(False, "কোন port? (যেমন 8080 বা 8000-8100)")
    return await run_on_broker("firewall", p, _target(p), env)


TOOL = Tool(name="firewall", params=P, run=run, action=action, target=_target, summary=_summary)
