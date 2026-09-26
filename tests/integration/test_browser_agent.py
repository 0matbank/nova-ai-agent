"""Phase 11: multi-step browser agent (Playwright MCP layer) + vision fallback,
through the real task engine, skill runner and permission stack. The browser
worker and the AI are scripted fakes; the live check is
scripts/browser_agent_drill.py."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from core.config import load_config
from core.orchestrator.browser_agent import parse_decision
from core.orchestrator.executor import UserRequestExecutor
from core.orchestrator.vision import parse_box
from core.queue.states import TaskState
from core.router.intent import IntentRouter
from models.router import ProviderRouter
from providers.provider_base import (
    HealthState,
    ProviderRequest,
    ProviderResult,
    ResultStatus,
)
from tests.mocks.browser import FakeBrowser
from tests.mocks.providers import FakeAdapter
from tests.mocks.skills import SkillEnv, make_skill_env

SID = "c" * 32
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()
GOAL = "shop.example.com-এ গিয়ে Prices থেকে Green tea-র দাম কত বলো"


class Shop(FakeBrowser):
    """Home page with a Prices button; after it is clicked the price table shows.
    A canvas 'Play' control exists only visually (for the vision fallback)."""

    def __init__(self, element_at_point: dict[str, Any] | None = None) -> None:
        self.page = "home"
        home = {"ok": True, "session_id": SID, "engine": "mcp",
                "url": "https://shop.example.com/", "title": "Shop", "challenge": False}
        self.home = home

        def snap(_: Any) -> dict[str, Any]:
            yaml = ('- heading "Shop" [ref=e2]\n- button "Prices" [ref=e5] [box=10,10,80,20]\n'
                    '- button "Buy now" [ref=e6]')
            if self.page == "prices":
                yaml += '\n- table [ref=e9]:\n  - row "Green tea 245 taka"'
            return {**home, "format": "yaml", "snapshot": yaml}

        def text(_: Any) -> dict[str, Any]:
            body = "Shop\nWelcome."
            if self.page == "prices":
                body += "\nGreen tea 245 taka\nMango juice 180 taka"
            if self.page == "playing":
                body += "\nStatus: playing"
            return {**home, "text": body}

        def click(body: Any) -> dict[str, Any]:
            if body["ref"] == "e5":
                self.page = "prices"
            return dict(home)

        def click_xy(body: Any) -> dict[str, Any]:
            self.page = "playing"
            return {**home, "title": "Shop — playing"}

        super().__init__({
            "/v1/sessions": home,
            f"/v1/sessions/{SID}/snapshot": snap,
            f"/v1/sessions/{SID}/text": text,
            f"/v1/sessions/{SID}/click": click,
            f"/v1/sessions/{SID}/click_xy": click_xy,
            f"/v1/sessions/{SID}/find": {"ok": True, "matches": "no matches"},
            f"/v1/sessions/{SID}/describe": {"ok": True, "element": {"tag": "BUTTON",
                                                                     "text": "Prices"}},
            f"/v1/sessions/{SID}/describe_xy": {"ok": True, "element": element_at_point or {
                "tag": "CANVAS", "text": "player"}},
            f"/v1/sessions/{SID}/screenshot": {**home, "png_b64": PNG},
            f"/v1/sessions/{SID}": {"ok": True, "closed": SID},
        })

    @property
    def closed(self) -> bool:
        return any(p == f"/v1/sessions/{SID}" for p, _ in self.calls)


class Brain(FakeAdapter):
    """Local AI with one scripted queue per task type (planner / vision / wording)."""

    def __init__(self, plans: list[dict[str, Any]], vision: list[str] | None = None,
                 phrase: Callable[[ProviderRequest], str] = lambda r: r.user_request) -> None:
        super().__init__("ollama_local", capabilities=frozenset(
            {"planning", "vision", "summarization", "intent_classification", "simple",
             "fallback"}))
        self.plans = list(plans)
        self.vision = list(vision or [])
        self.phrase = phrase
        self._set(HealthState.HEALTHY, "test")

    async def _complete(self, request: ProviderRequest) -> ProviderResult:
        self.requests.append(request)
        if request.task_type == "planning":
            answer = json.dumps(self.plans.pop(0) if self.plans else {"action": "read"})
        elif request.task_type == "vision":
            answer = self.vision.pop(0) if self.vision else '{"bbox_2d": null}'
        elif request.task_type == "summarization":
            answer = self.phrase(request)
        else:
            answer = '{"intent": "browser", "confidence": 0.9}'
        return ProviderResult(ResultStatus.OK, self.name, model="fake", answer=answer)


def setup(tmp_path: Path, site: FakeBrowser, brain: Brain) -> SkillEnv:
    s = make_skill_env(tmp_path, {"browser": site})
    cfg = load_config()
    adapters: dict[str, Any] = {n: FakeAdapter(n, state=HealthState.UNAVAILABLE)
                                for n in cfg.providers.providers}
    adapters["ollama_local"] = brain
    router = ProviderRouter(cfg.providers, adapters)
    s.tasks.engine.register("user_request",
                            UserRequestExecutor(IntentRouter(router), router, s.runner))
    s.photos = []  # type: ignore[attr-defined]
    orig = s.tasks.engine.notifier._send

    async def spy(chat_id, msg):  # type: ignore[no-untyped-def]
        if msg.photo:
            s.photos.append(msg.photo)  # type: ignore[attr-defined]
        await orig(chat_id, msg)
    s.tasks.engine.notifier._send = spy
    return s


def run(s: SkillEnv, text: str = GOAL) -> Any:
    tid = int(s.tasks.store.create(title=text, request_text=text, task_type="user_request",
                                   channel="telegram", chat_id="555").id)
    for _ in range(5):
        if not asyncio.run(s.tasks.engine.run_once()):
            break
    return s.tasks.store.get(tid)


def test_multi_step_flow_answers_from_the_page(tmp_path: Path) -> None:
    site = Shop()
    brain = Brain([{"action": "click", "ref": "e5"},
                   {"action": "answer", "answer": "Green tea costs 245 taka."}],
                  phrase=lambda r: "Green tea-র দাম ২৪৫ টাকা।")
    s = setup(tmp_path, site, brain)
    t = run(s)
    assert t.state is TaskState.COMPLETED, t.error_message
    assert "💬 Green tea-র দাম ২৪৫ টাকা।" in t.result_summary
    assert "1. click e5" in t.result_summary and site.closed
    open_body = next(b for p, b in site.calls if p == "/v1/sessions")
    assert open_body["engine"] == "mcp"                     # Layer 2 session
    planner = next(r for r in brain.requests if r.task_type == "planning")
    assert planner.context.startswith("<page>") and "untrusted data" in planner.system
    assert s.tasks.buttons == [] and len(s.photos) == 1     # type: ignore[attr-defined]


def test_ungrounded_answer_is_rejected_then_corrected(tmp_path: Path) -> None:
    brain = Brain([{"action": "click", "ref": "e5"},
                   {"action": "answer", "answer": "Green tea costs 999 taka."},   # invented
                   {"action": "answer", "answer": "Green tea costs 245 taka."}])
    s = setup(tmp_path, Shop(), brain)
    t = run(s)
    assert t.state is TaskState.COMPLETED and "245" in t.result_summary
    assert "999" not in t.result_summary.split("🧭")[0]
    third = [r for r in brain.requests if r.task_type == "planning"][2]
    assert "999 not found on the page" in third.context


def test_phrasing_that_loses_a_number_is_not_used(tmp_path: Path) -> None:
    brain = Brain([{"action": "click", "ref": "e5"},
                   {"action": "answer", "answer": "Green tea costs 245 taka."}],
                  phrase=lambda r: "Green tea অনেক সস্তা।")                   # dropped 245
    t = run(setup(tmp_path, Shop(), brain))
    assert "💬 Green tea costs 245 taka." in t.result_summary


def test_vision_fallback_clicks_what_the_snapshot_cannot_show(tmp_path: Path) -> None:
    site = Shop()
    brain = Brain([{"action": "vision_click", "target": "canvas"},
                   {"action": "answer", "answer": "The status is playing."}],
                  vision=['{"bbox_2d": [770, 330, 810, 390]}'])
    s = setup(tmp_path, site, brain)
    t = run(s, "Press the green play button on shop.example.com then tell me the status")
    assert t.state is TaskState.COMPLETED, t.error_message
    x, y = next(b for p, b in site.calls if p.endswith("/click_xy")).values()
    assert (x, y) == (round(790 / 1000 * 1280), round(360 / 1000 * 720))
    vision = next(r for r in brain.requests if r.task_type == "vision")
    assert vision.images and "green play button" in vision.user_request   # owner's words
    assert s.tasks.buttons == []                     # identified, harmless element


def test_vision_click_on_something_consequential_needs_approval(tmp_path: Path) -> None:
    site = Shop(element_at_point={"tag": "BUTTON", "text": "Buy now"})
    brain = Brain([{"action": "vision_click", "target": "the cart icon"}],
                  vision=['{"bbox_2d": [100, 100, 200, 200]}'])
    s = setup(tmp_path, site, brain)
    t = run(s, "shop.example.com-এ গিয়ে cart icon-এ click করো")
    assert t.state is TaskState.WAITING_APPROVAL and len(s.tasks.buttons) == 1
    assert not any(p.endswith("/click_xy") for p, _ in site.calls) and site.closed


def test_planner_giving_up_goes_to_the_owner_with_steps(tmp_path: Path) -> None:
    brain = Brain([{"action": "click", "ref": "e5"},
                   {"action": "fail", "reason": "login required"}])
    t = run(setup(tmp_path, Shop(), brain))
    assert t.state is TaskState.FAILED and t.error_code == "BLOCKED_NEEDS_USER"
    assert "login required" in t.error_message and "1. click e5" in t.error_message


def test_repeating_without_progress_stops(tmp_path: Path) -> None:
    brain = Brain([{"action": "find", "text": "tea"}] * 5)
    t = run(setup(tmp_path, Shop(), brain))
    assert t.state is TaskState.FAILED and t.error_code == "BLOCKED_NEEDS_USER"
    assert "বারবার" in t.error_message


def test_step_limit(tmp_path: Path) -> None:
    plans = [{"action": "find", "text": f"word{i}"} for i in range(20)]
    t = run(setup(tmp_path, Shop(), Brain(plans)))
    assert t.state is TaskState.FAILED and "12 ধাপেও" in t.error_message


def test_blocked_url_from_the_planner_is_a_failed_step_not_a_crash(tmp_path: Path) -> None:
    brain = Brain([{"action": "goto", "url": "http://127.0.0.1:47801/v1/session"},
                   {"action": "click", "ref": "e5"},
                   {"action": "answer", "answer": "Green tea costs 245 taka."}])
    site = Shop()
    t = run(setup(tmp_path, site, brain))
    assert t.state is TaskState.COMPLETED
    assert not any(p.endswith("/goto") for p, _ in site.calls)          # never sent
    second = [r for r in brain.requests if r.task_type == "planning"][1]
    assert "blocked by policy" in second.context


def test_routine_requests_still_use_the_fast_path(tmp_path: Path) -> None:
    site = Shop()
    run(setup(tmp_path, site, Brain([])), "shop.example.com খোলো")
    assert next(b for p, b in site.calls if p == "/v1/sessions").get("engine", "cli") == "cli"


@pytest.mark.parametrize(("answer", "box"), [
    ('{"bbox_2d": [100, 200, 300, 400]}', (100, 200, 300, 400)),
    ('{"box_2d": [200, 100, 400, 300]}', (100, 200, 300, 400)),        # Gemini y-first
    ('```json\n{"bbox_2d": [[100, 200, 300, 400]]}\n```', (100, 200, 300, 400)),
    ('{"bbox_2d": null}', None),
    ('{"bbox_2d": [300, 200, 100, 400]}', None),                       # inverted
    ("no json", None),
])
def test_parse_box(answer: str, box: tuple[float, ...] | None) -> None:
    assert parse_box(answer) == box


def test_type_accepts_value_alias_and_refuses_empty_text(tmp_path: Path) -> None:
    site = Shop()
    site.routes[f"/v1/sessions/{SID}/fill"] = lambda body: dict(site.home)
    brain = Brain([{"action": "type", "ref": "e3"},
                   {"action": "type", "ref": "e3", "value": "tea"},
                   {"action": "click", "ref": "e5"},
                   {"action": "answer", "answer": "Green tea costs 245 taka."}])
    t = run(setup(tmp_path, site, brain))
    assert t.state is TaskState.COMPLETED
    fills = [b for p, b in site.calls if p.endswith("/fill")]
    assert fills == [{"ref": "e3", "text": "tea", "submit": False}]


def test_parse_decision_tolerates_chatter() -> None:
    assert parse_decision('Sure!\n{"action": "click", "ref": "e3"}') == {"action": "click",
                                                                        "ref": "e3"}
    assert parse_decision("nonsense") == {}
