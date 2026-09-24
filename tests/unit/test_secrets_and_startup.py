from __future__ import annotations

from pathlib import Path

import pytest

from core.bootstrap import EXIT_INVALID_CONFIG, EXIT_OK, bootstrap, main
from core.config import load_secrets
from core.log import shutdown_logging


def test_secrets_hidden_in_repr(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=abc123456\nEMPTY=\n", encoding="utf-8")
    s = load_secrets(tmp_path)
    assert s.keys() == ["TELEGRAM_BOT_TOKEN"]          # empty values dropped
    assert "abc123456" not in repr(s) and "abc123456" not in str(s.require("TELEGRAM_BOT_TOKEN"))
    assert s.require("TELEGRAM_BOT_TOKEN").get_secret_value() == "abc123456"
    with pytest.raises(KeyError):
        s.require("MISSING")


def test_no_env_file_means_no_secrets(tmp_path: Path) -> None:
    assert load_secrets(tmp_path).keys() == []


def test_bootstrap_creates_runtime_dirs(config_dir: Path, runtime_root: Path) -> None:
    ctx = bootstrap(config_dir=config_dir, root=runtime_root, console_logs=False)
    shutdown_logging()
    for d in ["data", "secrets", "sessions", "logs/core", "logs/audit", "backups"]:
        assert (runtime_root / d).is_dir()
    assert "startup config valid" in (runtime_root / "logs/core/core.log").read_text("utf-8")
    assert ctx.config.root == runtime_root.resolve()


def test_secret_from_env_file_redacted_in_logs(config_dir: Path, runtime_root: Path) -> None:
    (runtime_root / "secrets").mkdir()
    (runtime_root / "secrets" / ".env").write_text("CUSTOM_SECRET=zz-top-secret-zz\n", "utf-8")
    bootstrap(config_dir=config_dir, root=runtime_root, console_logs=False)
    from core.log import get_logger
    get_logger("core").info("leaking zz-top-secret-zz?")
    shutdown_logging()
    assert "zz-top-secret-zz" not in (runtime_root / "logs/core/core.log").read_text("utf-8")


def test_main_ok(config_dir: Path, runtime_root: Path, capsys) -> None:
    rc = main(["--check", "--config-dir", str(config_dir), "--root", str(runtime_root)])
    shutdown_logging()
    assert rc == EXIT_OK and "STARTUP OK" in capsys.readouterr().out


def test_main_refuses_invalid_config(config_dir: Path, runtime_root: Path, capsys) -> None:
    (config_dir / "workers.yaml").write_text("protocol_version: 0\n", encoding="utf-8")
    rc = main(["--check", "--config-dir", str(config_dir), "--root", str(runtime_root)])
    assert rc == EXIT_INVALID_CONFIG
    assert "STARTUP REFUSED" in capsys.readouterr().err
    assert not (runtime_root / "logs").exists()   # nothing started
