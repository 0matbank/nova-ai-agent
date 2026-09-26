"""Claude adapter (Phase 14): built but disabled; subscription only (API-key
variables never reach the CLI); edit/read-only tool sets; limit / auth
detection — against a fake Claude Code CLI."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from core.config import load_config
from providers.anthropic_claude import ClaudeAdapter
from providers.provider_base import ErrorCategory, HealthState, ProviderRequest
from providers.registry import ProviderRegistry

FAKE = str(Path(__file__).resolve().parents[1] / "mocks" / "fake_claude.py")
APP_DIR = Path(__file__).resolve().parents[2]


def claude(disabled: bool = False) -> ClaudeAdapter:
    return ClaudeAdapter(claude_cmd=[sys.executable, FAKE], disabled=disabled)


def req(**kw: object) -> ProviderRequest:
    fields: dict[str, object] = {"task_type": "coding", **kw}
    return ProviderRequest(task_id=1, user_request="fix the bug", **fields)  # type: ignore[arg-type]


def test_registry_builds_the_real_adapter_but_disabled() -> None:
    reg = ProviderRegistry.from_config(load_config(APP_DIR / "config", APP_DIR.parent))
    a = reg.adapters["anthropic_claude"]
    assert isinstance(a, ClaudeAdapter)
    assert asyncio.run(a.check_health()).state is HealthState.DISABLED
    r = asyncio.run(a.complete(req()))
    assert not r.ok and r.error_category is ErrorCategory.MODEL_UNAVAILABLE


@pytest.mark.parametrize(("mode", "state"), [("ok", HealthState.HEALTHY),
                                             ("signedout", HealthState.AUTH_REQUIRED)])
def test_health_is_the_subscription_login(monkeypatch: pytest.MonkeyPatch, mode: str,
                                          state: HealthState) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
    assert asyncio.run(claude().check_health()).state is state


def test_tools_shell_and_web_are_never_allowed() -> None:
    a = claude()
    ro, rw = a.args_for(req()), a.args_for(req(workspace="D:/p", allowed_tools=("code.edit",)))
    for args in (ro, rw):
        assert args[args.index("--disallowedTools") + 1] == "Bash,WebFetch,WebSearch,mcp__*"
        assert "bypassPermissions" not in args and "--dangerously-skip-permissions" not in args
    assert ro[ro.index("--permission-mode") + 1] == "dontAsk"
    assert ro[ro.index("--allowedTools") + 1] == "Read,Glob,Grep"
    assert rw[rw.index("--permission-mode") + 1] == "acceptEdits"
    assert "Edit" in rw[rw.index("--allowedTools") + 1]


def test_result_parsed_and_api_key_never_reaches_the_cli(monkeypatch: pytest.MonkeyPatch,
                                                         tmp_path: Path) -> None:
    log = tmp_path / "call.json"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "placeholder-not-a-key")
    r = asyncio.run(claude().complete(req(system="rules", workspace=str(tmp_path),
                                          allowed_tools=("code.edit",))))
    assert r.ok and r.answer == "Fixed average() for empty lists." and r.session_id == "sess-7"
    call = json.loads(log.read_text(encoding="utf-8"))
    assert call["api_key_seen"] is False                        # owner rule: subscription only
    assert call["stdin"].startswith("rules") and "fix the bug" not in " ".join(call["argv"])


def test_usage_limit_and_auth_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "limit")
    a = claude()
    r = asyncio.run(a.complete(req()))
    assert not r.ok and r.error_category is ErrorCategory.RATE_LIMIT
    assert asyncio.run(a.check_health()).state is HealthState.COOLDOWN
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "auth")
    assert asyncio.run(claude().complete(req())).error_category is ErrorCategory.AUTH
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "crash")
    crash = asyncio.run(claude().complete(req()))
    assert crash.error_category is ErrorCategory.SERVER_ERROR and "fatal" in (crash.error or "")
