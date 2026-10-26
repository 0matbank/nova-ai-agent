"""V1.2 (owner 2026-10-26; idea from Atticus, MIT): the AI sees the last few asks and
answers of this chat, the last answer whole — follow-ups ("read me the status you gave",
"how many people live there", "translate this") stop failing. Measured with the real AI
on the owner's real failed follow-ups + typed ones: 0/6 → 6/6."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

from core.db.models import Task, utcnow
from core.orchestrator.context import (
    EARLIER_ANSWER_CHARS,
    LAST_ANSWER_CHARS,
    conversation,
    for_request,
)
from core.orchestrator.executor import UserRequestExecutor
from core.queue.states import TaskState
from core.router.intent import IntentRouter
from tests.integration.test_answer_routing import fake, router
from tests.mocks.skills import make_skill_env


def _done(s, chat: str, asked: str, answered: str, minutes_ago: float = 1) -> int:  # type: ignore[no-untyped-def]
    t = s.tasks.store.create(title="t", request_text=asked, task_type="user_request",
                             channel="telegram", chat_id=chat)
    with s.tasks.store._sessions.begin() as db:
        row = db.get(Task, t.id)
        row.state = TaskState.COMPLETED
        row.result_summary = answered
        row.finished_at = utcnow() - timedelta(minutes=minutes_ago)
    return int(t.id)


def test_recent_turns_this_chat_recent_oldest_first(tmp_path: Path) -> None:
    s = make_skill_env(tmp_path)
    _done(s, "555", "পুরোনো প্রশ্ন", "পুরোনো উত্তর", minutes_ago=30)     # too old
    _done(s, "999", "অন্য chat", "অন্য উত্তর")                             # another chat
    _done(s, "555", "প্রশ্ন ১", "উত্তর ১", minutes_ago=4)
    _done(s, "555", "প্রশ্ন ২", "উত্তর ২", minutes_ago=2)
    now = _done(s, "555", "এখনকার", "—", minutes_ago=0)
    assert s.tasks.store.recent_turns("555", now) == [("প্রশ্ন ১", "উত্তর ১"),
                                                     ("প্রশ্ন ২", "উত্তর ২")]
    assert s.tasks.store.recent_turns("555", now, limit=1) == [("প্রশ্ন ২", "উত্তর ২")]


def test_the_last_answer_goes_in_whole_earlier_ones_short() -> None:
    long = "x" * 9000
    text = conversation([("আগে", long), ("শেষে", long)])
    assert text.count("x") == EARLIER_ANSWER_CHARS + LAST_ANSWER_CHARS
    pkg = for_request(goal="g", instruction="i")
    pkg.add("Recent Conversation", text)
    assert "## Recent Conversation" in pkg.render() and pkg.render().count("x") == 5000
    assert conversation([]) == ""


def test_a_follow_up_reaches_both_ais_with_the_last_answer(tmp_path: Path) -> None:
    """The owner's real «…সেটি আমাকে করে সনাও, তুমি থেমে গেলো কেনা» after a status."""
    local = fake("ollama_local", ['{"intent": "question", "confidence": 0.9}',
                                  "CPU: 10% RAM: 84%"])
    s = make_skill_env(tmp_path)
    r = router(ollama_local=local)
    s.tasks.engine.register("user_request", UserRequestExecutor(IntentRouter(r), r, s.runner))
    _done(s, "555", "পিসির অবস্থা বলো", "CPU: 10%\nRAM: 84% (12.7/15.1 GB)")
    tid = int(s.tasks.store.create(
        title="t", request_text="সেটি আমাকে করে সনাও, তুমি থেমে গেলো কেনা",
        task_type="user_request", channel="telegram", chat_id="555").id)
    for _ in range(4):
        if not asyncio.run(s.tasks.engine.run_once()):
            break
    classify, ans = local.requests[0], local.requests[1]
    assert "পিসির অবস্থা বলো" in classify.user_request and "RAM: 84%" in classify.user_request
    assert "## Recent Conversation" in ans.context and "RAM: 84% (12.7/15.1 GB)" in ans.context
    assert "reads it out loud" in ans.system
    assert s.tasks.store.get(tid).state is TaskState.COMPLETED
