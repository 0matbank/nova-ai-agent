from __future__ import annotations

from pathlib import Path

import pytest

from core.config import ConfigError, load_config
from core.config.schema import EXEMPT_MESSAGE_TYPES, Level


def test_real_config_is_valid(config_dir: Path, runtime_root: Path) -> None:
    cfg = load_config(config_dir, runtime_root)
    assert cfg.root == runtime_root.resolve()
    assert cfg.providers.providers["anthropic_claude"].enabled is False
    assert cfg.providers.providers["ollama_local"].enabled is True
    gemini = cfg.providers.providers["gemini_api"]   # owner 2026-09-25: summaries only
    assert gemini.enabled is True and gemini.free_tier_only and set(gemini.priority) == {
        "summarization", "vision"}
    assert cfg.models.alias_sets["ollama_models"]["fast_general"] == "qwen3:8b"
    assert cfg.models.alias_sets["ollama_models"]["local_code_review"] == "deepseek-coder-v2:16b"
    assert cfg.permissions.actions["system.shutdown"] is Level.RED
    assert cfg.default.notification_throttle.min_progress_interval_seconds == 30
    assert cfg.default.notification_throttle.soft_max_progress_updates_per_task == 5
    assert cfg.default.notification_throttle.repeated_error_cooldown_seconds == 300
    assert set(cfg.projects) == {"click-tv", "stream-doctor", "codex-demo"}
    assert cfg.path("logs_dir") == runtime_root.resolve() / "logs"


def test_default_root_is_outside_repo(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PERSONAL_AGENT_ROOT", raising=False)
    cfg = load_config(config_dir)
    assert cfg.root == cfg.app_dir.parent


def test_env_root_override(config_dir: Path, runtime_root: Path,
                           monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_ROOT", str(runtime_root))
    assert load_config(config_dir).root == runtime_root.resolve()


def _assert_invalid(config_dir: Path, root: Path, needle: str) -> None:
    with pytest.raises(ConfigError) as ei:
        load_config(config_dir, root)
    assert any(needle in e for e in ei.value.errors), ei.value.errors


def test_missing_file_rejected(config_dir: Path, runtime_root: Path) -> None:
    (config_dir / "permissions.yaml").unlink()
    _assert_invalid(config_dir, runtime_root, "permissions.yaml: file missing")


def test_unknown_key_rejected(config_dir: Path, runtime_root: Path, edit_yaml) -> None:
    edit_yaml("default", lambda d: d["logging"].update(levle="INFO"))
    _assert_invalid(config_dir, runtime_root, "levle")


def test_duplicate_yaml_key_rejected(config_dir: Path, runtime_root: Path) -> None:
    p = config_dir / "schedules.yaml"
    p.write_text("schedules: []\nschedules: []\n", encoding="utf-8")
    _assert_invalid(config_dir, runtime_root, "duplicate key")


def test_broken_yaml_rejected(config_dir: Path, runtime_root: Path) -> None:
    (config_dir / "models.yaml").write_text("ollama_models: [unclosed\n", encoding="utf-8")
    _assert_invalid(config_dir, runtime_root, "YAML error")


def test_non_loopback_worker_rejected(config_dir: Path, runtime_root: Path, edit_yaml) -> None:
    edit_yaml("workers", lambda d: d["workers"]["desktop_worker"].update(host="0.0.0.0"))
    _assert_invalid(config_dir, runtime_root, "loopback")


def test_broker_http_rejected(config_dir: Path, runtime_root: Path, edit_yaml) -> None:
    edit_yaml("workers", lambda d: d["workers"]["privileged_broker"].update(transport="http"))
    _assert_invalid(config_dir, runtime_root, "privileged_broker.transport")


def test_red_action_cannot_be_downgraded(config_dir: Path, runtime_root: Path,
                                         edit_yaml) -> None:
    edit_yaml("permissions", lambda d: d["actions"].update({"git.push_production": "GREEN"}))
    _assert_invalid(config_dir, runtime_root, "must stay RED")


def test_approval_must_be_single_use(config_dir: Path, runtime_root: Path, edit_yaml) -> None:
    edit_yaml("permissions", lambda d: d["approval"].update(single_use=False))
    _assert_invalid(config_dir, runtime_root, "single_use")


def test_default_level_must_fail_closed(config_dir: Path, runtime_root: Path,
                                        edit_yaml) -> None:
    edit_yaml("permissions", lambda d: d.update(default_level="GREEN"))
    _assert_invalid(config_dir, runtime_root, "default_level")


def test_critical_notifications_cannot_be_throttled(config_dir: Path, runtime_root: Path,
                                                    edit_yaml) -> None:
    def drop(d):
        d["notification_throttle"]["exempt_message_types"] = list(EXEMPT_MESSAGE_TYPES[1:])
    edit_yaml("default", drop)
    _assert_invalid(config_dir, runtime_root, "must stay exempt")


def test_verifier_cannot_be_disabled(config_dir: Path, runtime_root: Path, edit_yaml) -> None:
    edit_yaml("agents", lambda d: d["agents"]["verifier"].update(enabled=False))
    _assert_invalid(config_dir, runtime_root, "verifier")


def test_claude_adapter_entry_required(config_dir: Path, runtime_root: Path, edit_yaml) -> None:
    edit_yaml("providers", lambda d: d["providers"].pop("anthropic_claude"))
    _assert_invalid(config_dir, runtime_root, "anthropic_claude")


def test_unknown_alias_set_rejected(config_dir: Path, runtime_root: Path, edit_yaml) -> None:
    edit_yaml("providers",
              lambda d: d["providers"]["openai_codex"].update(model_alias_set="nope"))
    _assert_invalid(config_dir, runtime_root, "not defined in models.yaml")


def test_ollama_policy_unknown_alias(config_dir: Path, runtime_root: Path, edit_yaml) -> None:
    edit_yaml("models", lambda d: d["ollama_policy"].update(default_alias="ghost"))
    _assert_invalid(config_dir, runtime_root, "unknown aliases")


def test_priority_out_of_range(config_dir: Path, runtime_root: Path, edit_yaml) -> None:
    edit_yaml("providers", lambda d: d["providers"]["openai_codex"]["priority"].update(coding=150))
    _assert_invalid(config_dir, runtime_root, "priority")


def test_ram_thresholds_ordered(config_dir: Path, runtime_root: Path, edit_yaml) -> None:
    edit_yaml("resources", lambda d: d["ram_percent"].update(high=95))
    _assert_invalid(config_dir, runtime_root, "moderate < high < critical")


def test_runtime_path_inside_repo_rejected(config_dir: Path) -> None:
    app_dir = Path(__file__).resolve().parents[2]
    _assert_invalid(config_dir, app_dir, "inside the repo")


def test_bad_project_profile(config_dir: Path, runtime_root: Path) -> None:
    (config_dir / "projects" / "x.yaml").write_text("name: X\nrepo_url: y\n", encoding="utf-8")
    _assert_invalid(config_dir, runtime_root, "projects/x.yaml")


def test_all_errors_reported_together(config_dir: Path, runtime_root: Path, edit_yaml) -> None:
    edit_yaml("workers", lambda d: d["workers"]["browser_worker"].update(host="8.8.8.8"))
    edit_yaml("resources", lambda d: d.update(disk_free_gb_min=-1))
    with pytest.raises(ConfigError) as ei:
        load_config(config_dir, runtime_root)
    joined = "\n".join(ei.value.errors)
    assert "workers.yaml" in joined and "resources.yaml" in joined
