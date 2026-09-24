"""Executor for free-text requests (task_type "user_request").

Phase 8 scope: understand the request (Intent Router), then
  - deterministic intents → the matching skill, no AI (plan §45, §49.1)
  - questions / general requests → provider-independent AI via the router
  - everything else → an honest "not built yet" answer (specialist agents,
    browser, coding etc. arrive in their own phases)
Verification is proof-based (plan §54): a skill result's own evidence, or a
non-empty answer actually returned by a provider.
"""

from __future__ import annotations

import base64
from typing import Any

from core.notify.notifier import MessageType
from core.orchestrator.context import for_request
from core.queue.engine import TaskContext, Verdict
from core.router.intent import Intent, IntentRouter
from core.skills.runner import SkillRunner
from models.router import ProviderRouter
from providers.provider_base import Limits, ProviderRequest

SYSTEM_PROMPT = (
    "You are Nova AI, a personal assistant running on the owner's Windows PC. "
    "Reply in the same language the user wrote in (Bangla, English or Banglish). "
    "Be concise and practical. You have NO internet access and your knowledge may be "
    "outdated or wrong. Never invent facts, dates, names or numbers: if you are not "
    "sure, say clearly that you are not sure. For current events or news, say you "
    "cannot know them. You cannot see the screen or run commands from this answer — "
    "never pretend you did."
)
NOT_YET = {
    "research": "Phase 16 (Research agent + web research; local AI-র কাছে আজকের তথ্য নেই, "
                "তাই বানিয়ে বলব না)",
    "coding": "Phase 12–15 (Codex/Antigravity + Git workflow)",
    "browser": "Phase 10–11 (Browser Worker + Playwright)",
    "pc_control": "Phase 16 (Windows Operator agent)",
    "files": "Phase 16 (File & Document agent)",
    "reminder": "Phase 19 (Scheduler)",
    "document": "Phase 20 (Document skills)",
    "project": "Phase 16–17 (project agents + memory)",
    "social": "V2 (Social/Growth modules)",
    "communication": "V2 (Email/WhatsApp integrations)",
}


class UserRequestExecutor:
    def __init__(self, intents: IntentRouter, providers: ProviderRouter,
                 skills: SkillRunner | None) -> None:
        self.intents = intents
        self.providers = providers
        self.skills = skills

    async def plan(self, ctx: TaskContext) -> list[str]:
        return ["request বোঝা (intent)", "কাজ করা", "ফলাফল যাচাই"]

    async def run(self, ctx: TaskContext) -> str:
        async def understand(prev: dict[str, Any] | None) -> dict[str, Any]:
            intent = await self.intents.classify(ctx.task.request_text, ctx.task.id)
            return {"category": intent.category, "source": intent.source,
                    "confidence": intent.confidence,
                    "skill": list(intent.skill) if intent.skill else None}

        cp = await ctx.run_step(1, understand) or {}
        skill_cp = cp.get("skill")
        skill = (str(skill_cp[0]), str(skill_cp[1]), dict(skill_cp[2])) if skill_cp else None
        intent = Intent(cp["category"], cp["confidence"], cp["source"], skill)

        async def act(prev: dict[str, Any] | None) -> dict[str, Any]:
            if intent.skill is not None:
                if self.skills is not None and intent.skill[0] in self.skills.registry.skills:
                    return await self._run_skill(ctx, intent)
                return {"kind": "not_yet", "answer": (
                    f"'{intent.skill[0]}' skill এই মুহূর্তে চালু নেই (disabled বা এই "
                    "platform-এ নেই), তাই কাজটা করা গেল না।")}
            if intent.category in NOT_YET:
                return {"kind": "not_yet", "answer": (
                    f"বুঝেছি — এটা '{intent.category}' ধরনের কাজ। এটা এখনো শেখানো হয়নি; "
                    f"আসবে {NOT_YET[intent.category]}।\nএখন পারি: প্রশ্নের উত্তর (local AI), "
                    "PC status, screenshot, process list।")}
            return await self._ask_ai(ctx)

        out = await ctx.run_step(2, act) or {}

        async def check(prev: dict[str, Any] | None) -> dict[str, Any]:
            return {"answer_present": bool(str(out.get("answer", "")).strip())}

        await ctx.run_step(3, check)
        return str(out.get("answer", ""))

    async def _run_skill(self, ctx: TaskContext, intent: Intent) -> dict[str, Any]:
        assert intent.skill is not None and self.skills is not None
        skill, tool, params = intent.skill
        result = await self.skills.invoke(ctx, skill, tool, dict(params))
        if skill == "screenshot" and result.ok and ctx.notifier is not None:
            await ctx.notifier.notify(ctx.task.chat_id, MessageType.INFO,
                                      f"🖥️ Task #{ctx.task.id}: {result.summary}",
                                      task_id=ctx.task.id,
                                      photo=base64.b64decode(result.data["preview_jpeg_b64"]))
        return {"kind": "skill", "skill": f"{skill}.{tool}", "ok": result.ok,
                "answer": result.summary, "evidence": result.evidence}

    async def _ask_ai(self, ctx: TaskContext) -> dict[str, Any]:
        package = for_request(goal="Answer the owner's request",
                              instruction=ctx.task.request_text, risk="GREEN")
        result = await self.providers.complete(ProviderRequest(
            task_id=ctx.task.id, task_type="reasoning", user_request=ctx.task.request_text,
            context=package.render(), system=SYSTEM_PROMPT,
            limits=Limits(timeout_seconds=180, max_output_tokens=800)))
        if not result.ok:
            raise RuntimeError(f"AI unavailable: {result.error}")
        return {"kind": "ai", "provider": result.provider, "model": result.model,
                "answer": result.answer, "ok": True}

    async def verify(self, ctx: TaskContext, result: str) -> Verdict:
        step2 = next((s for s in ctx.steps if s.seq == 2), None)
        act = (step2.checkpoint if step2 is not None else None) or {}
        if not result.strip():
            return Verdict(False, "empty result")
        kind = act.get("kind")
        if kind == "skill":
            return Verdict(bool(act.get("ok")), f"skill {act.get('skill')} evidence "
                                                f"{act.get('evidence')}")
        if kind == "ai":
            return Verdict(True, f"answer from {act.get('provider')} ({act.get('model')})")
        return Verdict(True, "PASS WITH KNOWN LIMITATIONS — capability not built yet")
