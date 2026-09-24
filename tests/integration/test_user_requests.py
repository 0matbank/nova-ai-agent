"""Phase 8 pass: free-text tasks run provider-independently (local AI), and
deterministic intents go straight to skills — through the real task engine."""

from __future__ import annotations

import asyncio
import base64
import io
import sys
from pathlib import Path

import pytest

from core.config import load_config
from core.orchestrator.executor import UserRequestExecutor
from core.queue.states import TaskState
from core.router.intent import IntentRouter
from models.router import ProviderRouter
from providers.provider_base import ErrorCategory
from tests.mocks.desktop import FakeDesktop
from tests.mocks.providers import FakeAdapter
from tests.mocks.skills import SkillEnv, make_skill_env


def _jpeg() -> str:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buf, "JPEG")
    return base64.b64encode(buf.getvalue()).decode()


def setup(tmp_path: Path, local_script: list | None = None,
          desktop: FakeDesktop | None = None) -> tuple[SkillEnv, FakeAdapter]:
    s = make_skill_env(tmp_path, {"desktop": desktop} if desktop else None)
    cfg = load_config()
    adapters = {n: FakeAdapter(n, state=__import__("providers.provider_base",
                                                   fromlist=["HealthState"]).HealthState.UNAVAILABLE)
                for n in cfg.providers.providers}
    local = FakeAdapter("ollama_local", local_script or [])
    adapters["ollama_local"] = local
    router = ProviderRouter(cfg.providers, adapters)
    s.tasks.engine.register("user_request",
                            UserRequestExecutor(IntentRouter(router), router, s.runner))
    return s, local


def submit(s: SkillEnv, text: str) -> int:
    return int(s.tasks.store.create(title=text, request_text=text, task_type="user_request",
                                    channel="telegram", chat_id="555").id)


def run_all(s: SkillEnv) -> None:
    for _ in range(4):
        if not asyncio.run(s.tasks.engine.run_once()):
            break


def test_question_answered_by_local_ai_via_router(tmp_path: Path) -> None:
    s, local = setup(tmp_path, local_script=[
        '{"intent": "question", "confidence": 0.9}', "ঢাকা বাংলাদেশের রাজধানী।"])
    tid = submit(s, "বাংলাদেশের রাজধানী কী?")
    run_all(s)
    t = s.tasks.store.get(tid)
    assert t.state is TaskState.COMPLETED and t.result_summary == "ঢাকা বাংলাদেশের রাজধানী।"
    classify, answer = local.requests
    assert classify.task_type == "intent_classification"
    assert answer.task_type == "reasoning"                # asked for reasoning, got local
    assert "## Relevant User Instruction" in answer.context
    assert "Chat History" not in answer.context
    done = [txt for _, txt in s.tasks.sent if "COMPLETE" in txt][0]
    assert "ঢাকা" in done and "ollama_local" in done


def test_pc_status_goes_to_skill_without_ai(tmp_path: Path) -> None:
    s, local = setup(tmp_path)
    tid = submit(s, "pc status bolo")
    run_all(s)
    t = s.tasks.store.get(tid)
    assert t.state is TaskState.COMPLETED and "CPU:" in t.result_summary
    assert local.requests == []                          # no LLM for a deterministic command


@pytest.mark.skipif(sys.platform != "win32", reason="screenshot skill is Windows-only")
def test_screenshot_request_sends_photo(tmp_path: Path) -> None:
    desk = FakeDesktop({"/v1/screenshot": {"width": 10, "height": 10, "monitors": 1,
                                           "blank": False, "path": "x.png",
                                           "preview_jpeg_b64": _jpeg()}})
    s, _ = setup(tmp_path, desktop=desk)
    photos: list[bytes] = []
    orig = s.tasks.engine.notifier._send

    async def spy(chat_id, msg):  # type: ignore[no-untyped-def]
        if msg.photo:
            photos.append(msg.photo)
        await orig(chat_id, msg)
    s.tasks.engine.notifier._send = spy
    tid = submit(s, "screenshot dao")
    run_all(s)
    assert s.tasks.store.get(tid).state is TaskState.COMPLETED and len(photos) == 1


def test_not_yet_capability_is_honest(tmp_path: Path) -> None:
    s, local = setup(tmp_path)
    tid = submit(s, "Click TV repo te player bug fix koro")
    run_all(s)
    t = s.tasks.store.get(tid)
    assert t.state is TaskState.COMPLETED and "Phase 12" in t.result_summary
    assert local.requests == []


def test_unavailable_skill_answers_instead_of_crashing(tmp_path: Path) -> None:
    s, _ = setup(tmp_path)
    s.runner.registry.skills.pop("screenshot", None)
    tid = submit(s, "screenshot dao")
    run_all(s)
    t = s.tasks.store.get(tid)
    assert t.state is TaskState.COMPLETED and "চালু নেই" in t.result_summary


def test_ai_down_retries_then_fails(tmp_path: Path) -> None:
    s, _ = setup(tmp_path, local_script=[ErrorCategory.SERVER_ERROR] * 20)
    tid = submit(s, "ekta golpo bolo")
    for _ in range(6):
        asyncio.run(s.tasks.engine.run_once())
    t = s.tasks.store.get(tid)
    assert t.state is TaskState.FAILED and "AI unavailable" in (t.error_message or "")
