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
    summary = bangla(op, params, evidence) or str(result.get("summary", "done"))
    return ToolResult(True, summary, {**evidence, "broker_summary": result.get("summary")},
                      evidence)


def bangla(op: str, p: dict[str, Any], ev: dict[str, Any]) -> str:
    """A clear owner-language line the planner can repeat as is (small models garble
    'restart' etc. when they translate it themselves)."""
    state = {"running": "চালু", "stopped": "বন্ধ"}.get(str(ev.get("state")), ev.get("state"))
    if op == "service":
        done = {"start": "চালু করা হয়েছে", "stop": "বন্ধ করা হয়েছে",
                "restart": "রিস্টার্ট করা হয়েছে"}.get(p.get("op", ""))
        return (f"✅ '{p['name']}' service {done} — এখন {state}" if done
                else f"'{p['name']}' service এখন {state}")
    if op == "firewall" and p.get("op") in ("add", "remove"):
        if p["op"] == "remove":
            return f"✅ Firewall rule 'Nova AI - {p['name']}' মুছে ফেলা হয়েছে"
        what = "ব্লক" if p.get("rule_action") == "block" else "অনুমতি"
        way = "incoming" if p.get("direction") == "in" else "outgoing"
        return (f"✅ Firewall rule 'Nova AI - {p['name']}' যোগ হয়েছে — {way} "
                f"{p.get('protocol')} port {p.get('port')} {what}")
    if op == "power":
        if p.get("op") == "cancel":
            return ("✅ নির্ধারিত restart/shutdown বাতিল করা হয়েছে" if ev.get("cancelled")
                    else "কোনো restart/shutdown নির্ধারিত ছিল না — কিছু বাতিল করার নেই")
        what = "রিস্টার্ট" if p.get("op") == "restart" else "শাটডাউন"
        mins = int(p.get("delay_seconds", 60)) // 60
        return (f"✅ PC {mins} মিনিট পর {what} হবে। থামাতে লিখুন: restart cancel")
    if op == "package":
        return (f"✅ {p['id']} install হয়েছে" if ev.get("installed")
                else f"✅ {p['id']} uninstall হয়েছে")
    if op == "task":
        return f"✅ {p['name']} সম্পন্ন (exit {ev.get('exit_code')})"
    return ""
