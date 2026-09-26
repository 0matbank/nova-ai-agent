"""Idempotency ledger (plan §9.1, Phase 14): a side effect of a task runs at
most once — a retry, a restart or another provider never repeats it."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.queue.engine import TaskBlocked
from core.skills.ledger import PENDING, effect_key, is_side_effect
from tests.mocks.skills import SkillEnv, make_skill_env


@pytest.fixture
def s(tmp_path: Path) -> SkillEnv:
    return make_skill_env(tmp_path)


def test_side_effect_classes() -> None:
    for action in ("file.delete_workspace", "file.move", "powershell.run", "process.kill",
                   "git.push_production", "message.send_external", "admin.destructive",
                   "download"):
        assert is_side_effect(action), action
    for action in ("file.read", "browser.click", "ui.type", "test.run", "code.edit"):
        assert not is_side_effect(action), action
    assert effect_key("a", "t", {"x": 1, "y": 2}) == effect_key("a", "t", {"y": 2, "x": 1})


def test_same_delete_in_the_same_task_runs_once(s: SkillEnv) -> None:
    tid = s.new_task()
    s.call("files", "write", {"path": "tmp.txt", "content": "v1"}, task_id=tid)
    first = s.call("files", "delete", {"path": "tmp.txt"}, task_id=tid)
    assert first.ok and not (s.workspace / "tmp.txt").exists()
    # the file comes back (e.g. the owner restores it) and the task is retried
    (s.workspace / "tmp.txt").write_text("restored by the owner", encoding="utf-8")
    again = s.call("files", "delete", {"path": "tmp.txt"}, task_id=tid)
    assert again.ok and again.data.get("idempotent_replay") is True
    assert "not repeated" in again.summary
    assert (s.workspace / "tmp.txt").read_text(encoding="utf-8") == "restored by the owner"


def test_another_task_is_not_affected(s: SkillEnv) -> None:
    s.call("files", "write", {"path": "a.txt", "content": "1"})
    assert s.call("files", "delete", {"path": "a.txt"}).ok
    s.call("files", "write", {"path": "a.txt", "content": "2"})
    r = s.call("files", "delete", {"path": "a.txt"})                  # a new task
    assert r.ok and not r.data.get("idempotent_replay") and not (s.workspace / "a.txt").exists()


def test_unknown_outcome_is_never_re_run_blindly(s: SkillEnv) -> None:
    tid = s.new_task()
    s.call("files", "write", {"path": "b.txt", "content": "x"}, task_id=tid)
    ledger = s.runner.ledger
    assert ledger is not None
    target = str((s.workspace / "b.txt").resolve())
    # simulate a crash between "started" and "finished" of an earlier attempt
    sk, t = s.runner.registry.get("files", "delete")
    p = t.params.model_validate({"path": "b.txt"})
    key = effect_key("file.delete_workspace", t.target(p), p.model_dump(mode="json"))
    ledger.begin(tid, key, "file.delete_workspace", target)
    assert ledger.lookup(tid, key).status == PENDING  # type: ignore[union-attr]
    with pytest.raises(TaskBlocked, match="never recorded"):
        s.call("files", "delete", {"path": "b.txt"}, task_id=tid)
    assert (s.workspace / "b.txt").exists()                            # untouched


def test_a_failed_attempt_may_be_retried(s: SkillEnv) -> None:
    tid = s.new_task()
    s.call("files", "write", {"path": "c.txt", "content": "x"}, task_id=tid)
    ledger = s.runner.ledger
    assert ledger is not None
    _, t = s.runner.registry.get("files", "delete")
    p = t.params.model_validate({"path": "c.txt"})
    key = effect_key("file.delete_workspace", t.target(p), p.model_dump(mode="json"))
    ledger.finish(ledger.begin(tid, key, "file.delete_workspace", t.target(p)), False,
                  "worker was down")                               # an attempt that failed
    r = s.call("files", "delete", {"path": "c.txt"}, task_id=tid)
    assert r.ok and not r.data.get("idempotent_replay") and not (s.workspace / "c.txt").exists()
    assert ledger.lookup(tid, key).status == "DONE"  # type: ignore[union-attr]
