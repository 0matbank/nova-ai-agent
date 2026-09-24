from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from core.log import Redactor, get_logger, log_context, setup_logging, shutdown_logging
from core.log.redaction import MASK

CATS = ["core", "desktop", "browser", "provider", "tasks", "audit"]
FAKE_TG = "123456789:" + "A" * 35


@pytest.fixture
def logs(tmp_path: Path) -> Iterator[Path]:
    d = tmp_path / "logs"
    setup_logging(d, CATS, worker="core", known_secrets=["my-super-secret-value"],
                  console=False, max_bytes=2048, backup_count=2)
    yield d
    shutdown_logging()


def _lines(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


def test_structured_fields(logs: Path) -> None:
    with log_context(task_id="1024", agent="developer", provider="openai_codex"):
        get_logger("tasks").info("step done",
                                 extra={"action": "build", "status": "ok", "duration": 1.5})
    shutdown_logging()
    [e] = _lines(logs / "tasks" / "tasks.log")
    assert e["task_id"] == "1024" and e["agent"] == "developer"
    assert e["provider"] == "openai_codex" and e["action"] == "build"
    assert e["status"] == "ok" and e["duration"] == 1.5 and e["worker"] == "core"
    assert e["category"] == "tasks" and "timestamp" in e


def test_category_files_are_separate(logs: Path) -> None:
    get_logger("audit").info("approval granted", extra={"action": "approval"})
    get_logger("core").info("core line")
    shutdown_logging()
    assert len(_lines(logs / "audit" / "audit.log")) == 1
    assert len(_lines(logs / "core" / "core.log")) == 1
    for cat in CATS:
        assert (logs / cat).is_dir()


def test_known_secret_redacted(logs: Path) -> None:
    get_logger("core").info("token is my-super-secret-value ok")
    shutdown_logging()
    text = (logs / "core" / "core.log").read_text(encoding="utf-8")
    assert "my-super-secret-value" not in text and MASK in text


def test_pattern_secret_redacted_in_extra_and_traceback(logs: Path) -> None:
    try:
        raise RuntimeError(f"bad token {FAKE_TG}")
    except RuntimeError:
        get_logger("provider").exception("fail", extra={"error_code": FAKE_TG})
    shutdown_logging()
    text = (logs / "provider" / "provider.log").read_text(encoding="utf-8")
    assert FAKE_TG not in text


def test_bangla_text_kept_readable(logs: Path) -> None:
    get_logger("core").info("কাজ শেষ")
    shutdown_logging()
    assert "কাজ শেষ" in (logs / "core" / "core.log").read_text(encoding="utf-8")


def test_rotation(logs: Path) -> None:
    log = get_logger("core")
    for i in range(200):
        log.info("line %d %s", i, "x" * 50)
    shutdown_logging()
    assert (logs / "core" / "core.log.1").exists()
    assert not (logs / "core" / "core.log.3").exists()   # backup_count=2


def test_unknown_context_field_rejected() -> None:
    with pytest.raises(ValueError), log_context(password="x"):
        pass


@pytest.mark.parametrize("secret", [
    FAKE_TG,
    "sk-" + "a" * 30,
    "sk-ant-" + "b" * 30,
    "AIza" + "c" * 35,
    "ghp_" + "d" * 36,
    "Bearer abcdefghijklmnopqrstuvwxyz",
    "-----BEGIN " + "PRIVATE KEY-----\nabc\n-----END " + "PRIVATE KEY-----",
])
def test_redactor_patterns(secret: str) -> None:
    assert secret not in Redactor()(f"before {secret} after")
