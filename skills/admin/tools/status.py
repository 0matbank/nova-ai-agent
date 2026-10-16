"""Is the admin broker installed / running / elevated, and what may it do?"""

from __future__ import annotations

from core.broker.client import BrokerError, BrokerUnavailable, client_for
from core.broker.skill import NOT_RUNNING
from core.skills.api import Params, Tool, ToolEnv, ToolResult


class P(Params):
    pass


async def run(p: P, env: ToolEnv) -> ToolResult:
    client = client_for(env.config)
    try:
        st = await client.status()
    except (BrokerUnavailable, BrokerError) as e:
        return ToolResult(False, f"{NOT_RUNNING} ({e})", {"running": False,
                                                          "installed": client.installed})
    pol = st.get("policy", {})
    summary = (f"Admin broker {st.get('version')} চলছে · administrator: "
               f"{'হ্যাঁ' if st.get('elevated') else 'না'} · lockdown: "
               f"{'চালু' if st.get('lockdown') else 'বন্ধ'}\n"
               f"Services: {', '.join(pol.get('services', [])) or '—'}\n"
               f"Tasks: {', '.join(pol.get('tasks', [])) or '—'} · winget: "
               f"{'হ্যাঁ' if pol.get('packages') else 'না'}")
    return ToolResult(True, summary, {"running": True, **st})


TOOL = Tool(name="status", params=P, run=run, action="admin.status", target=lambda p: "broker")
