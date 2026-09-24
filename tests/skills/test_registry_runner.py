"""Skill folder contract (plan §14) + runner gate."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from core.config.schema import SkillsConfig
from core.skills.registry import SKILLS_DIR, SkillError, SkillRegistry, load_skill
from tests.mocks.skills import make_skill_env

ENABLED = {"windows", "app-control", "files", "search", "powershell", "terminal"}


def test_all_enabled_skills_load() -> None:
    cfg = SkillsConfig.model_validate({"skills": {n: {"status": "enabled"} for n in ENABLED}})
    reg = SkillRegistry.load(cfg, platform="win32")
    assert set(reg.skills) == ENABLED
    for sk in reg.skills.values():
        assert sk.doc().startswith("# ")
        for tool in sk.tools:
            assert sk.declared[tool], f"{sk.name}.{tool} declares no actions"


def test_platform_filter() -> None:
    cfg = SkillsConfig.model_validate({"skills": {n: {"status": "enabled"} for n in ENABLED}})
    assert "powershell" not in SkillRegistry.load(cfg, platform="linux").skills


def test_planned_skills_are_skipped() -> None:
    cfg = SkillsConfig.model_validate({"skills": {"voice": {"status": "planned"}}})
    assert SkillRegistry.load(cfg).skills == {}


def _copy_skill(tmp_path: Path, name: str = "windows") -> Path:
    dst = tmp_path / name
    shutil.copytree(SKILLS_DIR / name, dst)
    return dst


@pytest.mark.parametrize("remove", ["SKILL.md", "manifest.json", "permissions.json", "tests"])
def test_missing_contract_part_rejected(tmp_path: Path, remove: str) -> None:
    folder = _copy_skill(tmp_path)
    target = folder / remove
    shutil.rmtree(target) if target.is_dir() else target.unlink()
    with pytest.raises(SkillError, match="missing"):
        load_skill(folder)


def test_tool_without_declared_actions_rejected(tmp_path: Path) -> None:
    folder = _copy_skill(tmp_path)
    (folder / "permissions.json").write_text(json.dumps({"status": ["pc.status"]}))
    with pytest.raises(SkillError, match="declares no actions"):
        load_skill(folder)


def test_manifest_name_must_match_folder(tmp_path: Path) -> None:
    folder = _copy_skill(tmp_path)
    folder.rename(tmp_path / "renamed")
    with pytest.raises(SkillError, match="!= folder name"):
        load_skill(tmp_path / "renamed")


def test_enabled_but_missing_folder_fails_closed(tmp_path: Path) -> None:
    cfg = SkillsConfig.model_validate({"skills": {"ghost": {"status": "enabled"}}})
    with pytest.raises(SkillError, match="does not exist"):
        SkillRegistry.load(cfg, skills_dir=tmp_path)


def test_runner_blocks_undeclared_action(tmp_path: Path) -> None:
    s = make_skill_env(tmp_path)
    s.runner.registry.skills["windows"].declared["status"] = ["process.list"]
    with pytest.raises(SkillError, match="undeclared action"):
        s.call("windows", "status", {})


def test_runner_rejects_bad_params(tmp_path: Path) -> None:
    s = make_skill_env(tmp_path)
    with pytest.raises(SkillError, match="invalid params"):
        s.call("files", "write", {"path": "a.txt", "content": "x", "evil": True})
    with pytest.raises(SkillError, match="unknown or disabled"):
        s.call("voice", "transcribe", {})


def test_skills_command_lists_levels(tmp_path: Path) -> None:
    import asyncio
    import datetime as dt

    from channels.base import IncomingMessage
    from core.orchestrator.skill_commands import SkillCommands
    s = make_skill_env(tmp_path)
    cmd = SkillCommands(s.runner.registry, s.tasks.permissions)
    msg = IncomingMessage("telegram", "1", "1", "1", "/skills", dt.datetime.now(dt.UTC))
    out = asyncio.run(cmd.skills(msg, []))
    assert "• files" in out and "delete 🔵🔴" in out and "read_text 🟢" in out
    assert "write 🔵🟡" in out
