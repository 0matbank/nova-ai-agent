"""Skill loader (plan §14). Each skill folder must contain:

    SKILL.md          how/when to use it (for agents + humans)
    manifest.json     name, version, description, worker, tools
    permissions.json  tool → the permission actions it may request
    tools/<tool>.py   one module per tool exposing TOOL: core.skills.api.Tool
    tests/            the skill's own tests

Only skills marked `enabled` in config/skills.yaml are loaded; the rest are
skipped. A malformed enabled skill is a startup error (fail closed).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from core.config.schema import SkillsConfig
from core.skills.api import Tool

SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"
REQUIRED_FILES = ("SKILL.md", "manifest.json", "permissions.json")


class SkillError(Exception):
    pass


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    version: str
    description: str
    worker: Literal["core", "desktop", "browser"]
    platforms: list[Literal["win32", "linux", "darwin"]]
    tools: list[str]


class Skill:
    def __init__(self, manifest: Manifest, folder: Path, tools: dict[str, Tool],
                 declared: dict[str, list[str]]) -> None:
        self.manifest = manifest
        self.folder = folder
        self.tools = tools
        self.declared = declared

    @property
    def name(self) -> str:
        return self.manifest.name

    def doc(self) -> str:
        return (self.folder / "SKILL.md").read_text(encoding="utf-8")


def _load_tool(skill: str, path: Path) -> Tool:
    mod_name = f"nova_skill_{skill.replace('-', '_')}_{path.stem}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise SkillError(f"{skill}: cannot import {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    tool = getattr(module, "TOOL", None)
    if not isinstance(tool, Tool):
        raise SkillError(f"{skill}/{path.name}: must define TOOL = core.skills.api.Tool(...)")
    if tool.name != path.stem:
        raise SkillError(f"{skill}/{path.name}: TOOL.name {tool.name!r} != file name")
    return tool


def load_skill(folder: Path) -> Skill:
    for f in REQUIRED_FILES:
        if not (folder / f).is_file():
            raise SkillError(f"{folder.name}: missing {f}")
    if not (folder / "tests").is_dir():
        raise SkillError(f"{folder.name}: missing tests/")
    try:
        manifest = Manifest.model_validate_json((folder / "manifest.json").read_text("utf-8"))
    except ValidationError as e:
        raise SkillError(f"{folder.name}: invalid manifest.json: {e}") from None
    if manifest.name != folder.name:
        raise SkillError(f"{folder.name}: manifest name {manifest.name!r} != folder name")
    declared = json.loads((folder / "permissions.json").read_text("utf-8"))
    tools: dict[str, Tool] = {}
    for tname in manifest.tools:
        path = folder / "tools" / f"{tname}.py"
        if not path.is_file():
            raise SkillError(f"{manifest.name}: tool {tname!r} listed but tools/{tname}.py missing")
        if not declared.get(tname):
            raise SkillError(f"{manifest.name}: permissions.json declares no actions for {tname!r}")
        tools[tname] = _load_tool(manifest.name, path)
    return Skill(manifest, folder, tools, {k: list(v) for k, v in declared.items()})


class SkillRegistry:
    def __init__(self, skills: dict[str, Skill]) -> None:
        self.skills = skills

    @classmethod
    def load(cls, config: SkillsConfig, skills_dir: Path = SKILLS_DIR,
             platform: str = sys.platform) -> SkillRegistry:
        loaded: dict[str, Skill] = {}
        for name, entry in config.skills.items():
            if entry.status != "enabled":
                continue
            folder = skills_dir / name
            if not folder.is_dir():
                raise SkillError(f"skill {name!r} is enabled but {folder} does not exist")
            skill = load_skill(folder)
            if platform in skill.manifest.platforms:
                loaded[name] = skill
        return cls(loaded)

    def get(self, skill: str, tool: str) -> tuple[Skill, Tool]:
        s = self.skills.get(skill)
        if s is None:
            raise SkillError(f"unknown or disabled skill {skill!r}")
        t = s.tools.get(tool)
        if t is None:
            raise SkillError(f"skill {skill!r} has no tool {tool!r}")
        return s, t
