"""OpenAI Codex adapter (Phase 12): ChatGPT sign-in health, non-interactive
exec, JSONL result parsing, usage-limit / auth detection — against a fake CLI."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from providers.openai_codex import CodexAdapter
from providers.openai_codex.adapter import parse_events, retry_after
from providers.provider_base import ErrorCategory, HealthState, ProviderRequest

FAKE = str(Path(__file__).resolve().parents[1] / "mocks" / "fake_codex.py")


def codex() -> CodexAdapter:
    return CodexAdapter(codex_cmd=[sys.executable, FAKE])


def req(**kw: object) -> ProviderRequest:
    return ProviderRequest(task_id=1, task_type="coding", user_request="fix the bug",
                           **kw)  # type: ignore[arg-type]


@pytest.mark.parametrize(("mode", "state"), [("chatgpt", HealthState.HEALTHY),
                                             ("apikey", HealthState.AUTH_REQUIRED),
                                             ("none", HealthState.AUTH_REQUIRED)])
def test_health_requires_chatgpt_sign_in(monkeypatch: pytest.MonkeyPatch, mode: str,
                                         state: HealthState) -> None:
    monkeypatch.setenv("FAKE_CODEX_MODE", mode)
    assert asyncio.run(codex().check_health()).state is state


def test_missing_cli_is_unavailable() -> None:
    a = CodexAdapter(codex_cmd=[sys.executable, str(Path(FAKE).with_name("nope.js"))])
    assert asyncio.run(a.check_health()).state is HealthState.UNAVAILABLE


def test_read_only_unless_code_edit_is_allowed_for_a_workspace() -> None:
    a = codex()
    ro = a.args_for(req())
    assert ro[ro.index("--sandbox") + 1] == "read-only" and "--skip-git-repo-check" in ro
    ro2 = a.args_for(req(workspace="D:/p"))                         # workspace, no permission
    assert ro2[ro2.index("--sandbox") + 1] == "read-only"
    rw = a.args_for(req(workspace="D:/p", allowed_tools=("code.edit",)))
    assert rw[rw.index("--sandbox") + 1] == "workspace-write"
    for args in (ro, rw):
        assert "sandbox_workspace_write.network_access=false" in args      # never network
        assert 'approval_policy="never"' in args                            # non-interactive
        assert "--dangerously-bypass-approvals-and-sandbox" not in args
        assert args[-2] == "--"                                            # prompt is data


def test_exec_result_is_normalised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_CODEX_MODE", "ok")
    r = asyncio.run(codex().complete(req(system="rules", context="ctx")))
    assert r.ok and r.provider == "openai_codex" and r.answer == "Fixed average() and median()."
    assert r.files_changed == ("stats.py",) and r.session_id == "thread-123"
    assert r.commands == ("python -m unittest",) and r.usage.input_tokens == 1200


def test_usage_limit_is_rate_limit_with_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_CODEX_MODE", "limit")
    a = codex()
    r = asyncio.run(a.complete(req()))
    assert not r.ok and r.error_category is ErrorCategory.RATE_LIMIT
    assert r.retry_after_seconds == 2 * 3600 + 5 * 60
    assert asyncio.run(a.check_health()).state is HealthState.COOLDOWN     # router skips it


def test_auth_error_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_CODEX_MODE", "auth")
    r = asyncio.run(codex().complete(req()))
    assert not r.ok and r.error_category is ErrorCategory.AUTH


def test_parse_real_event_shapes() -> None:
    out = parse_events("\n".join([
        '{"type": "thread.started", "thread_id": "t1"}',
        "not json",
        '{"type": "item.completed", "item": {"type": "agent_message", "text": "a"}}',
        '{"type": "item.completed", "item": {"type": "file_change", "changes": '
        '[{"path": "x.py", "kind": "add"}, {"path": "x.py", "kind": "update"}]}}',
        '{"type": "item.completed", "item": {"type": "agent_message", "text": "final"}}',
        '{"type": "turn.completed", "usage": {"input_tokens": 5}}']))
    assert out["answer"] == "final" and out["files"] == ["x.py"] and out["session_id"] == "t1"
    assert parse_events('{"type": "turn.failed", "error": {"message": "boom"}}')["error"] == "boom"


@pytest.mark.parametrize(("text", "seconds"), [("Try again in 2 hours 5 minutes", 7500),
                                               ("try again in 45m", 2700),
                                               ("please try again later", None)])
def test_retry_after(text: str, seconds: float | None) -> None:
    assert retry_after(text) == seconds
