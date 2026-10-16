"""Universal Software Adapter Registry (owner requirement 2026-10-16; Phase 20
foundation): which creative programs exist on this PC, how Nova could drive them
and which capabilities are really proven (config/creative.yaml).

Nova only executes with an adapter whose needed capabilities are all `tested`;
anything else is reported honestly, never attempted silently."""

from __future__ import annotations

import glob
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, field_validator

from core.log import get_logger

Status = Literal["tested", "partial", "unsupported", "future"]
Method = Literal["native_api", "script", "cli", "ui"]
CONFIG = Path(__file__).resolve().parents[2] / "config" / "creative.yaml"
_log = get_logger("core")


class Detect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bundled: str | None = None            # a Python package that carries the binary
    exe: list[str] = []
    paths: list[str] = []                 # globs, %ENV% allowed
    app_paths: list[str] = []             # HKLM/HKCU ...\App Paths\<name>


class SoftwareEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    category: str
    automation: list[Method]
    license: str | None = None
    detect: Detect = Detect()
    capabilities: dict[str, Status]
    notes: str = ""


class CreativeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capability_names: list[str]
    software: dict[str, SoftwareEntry]

    @field_validator("software")
    @classmethod
    def _known_caps(cls, v: dict[str, SoftwareEntry], info) -> dict[str, SoftwareEntry]:  # type: ignore[no-untyped-def]
        names = set(info.data.get("capability_names") or [])
        for key, s in v.items():
            unknown = set(s.capabilities) - names
            if unknown:
                raise ValueError(f"{key}: unknown capabilities {sorted(unknown)}")
        return v


@dataclass(frozen=True)
class Installed:
    key: str
    name: str
    path: str | None
    version: str | None

    @property
    def found(self) -> bool:
        return self.path is not None


def load(path: Path = CONFIG) -> CreativeConfig:
    return CreativeConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def _app_path(name: str) -> str | None:
    if sys.platform != "win32":
        return None
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, rf"Software\Microsoft\Windows\CurrentVersion\App Paths"
                                      rf"\{name}") as k:
                value, _ = winreg.QueryValueEx(k, "")
                if value and Path(str(value)).exists():
                    return str(value)
        except OSError:
            continue
    return None


def _file_version(path: str) -> str | None:
    if sys.platform != "win32":
        return None
    try:
        import win32api
        info = win32api.GetFileVersionInfo(path, "\\")
        ms, ls = info["FileVersionMS"], info["FileVersionLS"]
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:  # noqa: BLE001 - no version resource is not an error
        return None


def detect(key: str, entry: SoftwareEntry) -> Installed:
    d = entry.detect
    if d.bundled:
        try:
            if d.bundled == "imageio_ffmpeg":
                from core.skills.mediaops import ffmpeg_exe
                exe = ffmpeg_exe()
                return Installed(key, entry.name, exe, Path(exe).stem.split("-v")[-1]
                                 if "-v" in Path(exe).stem else None)
        except Exception:  # noqa: BLE001 - fall through to the other ways
            _log.debug(f"{key}: bundled binary not available", exc_info=True)
    for name in d.exe:
        found = shutil.which(name)
        if found:
            return Installed(key, entry.name, found, _file_version(found))
    for name in d.app_paths:
        found = _app_path(name)
        if found:
            return Installed(key, entry.name, found, _file_version(found))
    for pattern in d.paths:
        for hit in sorted(glob.glob(os.path.expandvars(pattern)), reverse=True):
            if Path(hit).is_file():
                return Installed(key, entry.name, hit, _file_version(hit))
    return Installed(key, entry.name, None, None)


class SoftwareRegistry:
    def __init__(self, config: CreativeConfig) -> None:
        self.config = config
        self._found: dict[str, Installed] | None = None

    @classmethod
    def load(cls, path: Path = CONFIG) -> SoftwareRegistry:
        return cls(load(path))

    def installed(self, refresh: bool = False) -> dict[str, Installed]:
        if self._found is None or refresh:
            self._found = {k: detect(k, e) for k, e in self.config.software.items()}
        return self._found

    def choose(self, needed: set[str]) -> tuple[str | None, list[str]]:
        """The installed adapter that has ALL needed capabilities `tested`, preferring
        the one with the fewest gaps; plus what nobody can do (honest report)."""
        best, missing_best = None, sorted(needed)
        for key, entry in self.config.software.items():
            if not self.installed()[key].found:
                continue
            missing = sorted(c for c in needed if entry.capabilities.get(c) != "tested")
            if not missing:
                return key, []
            if len(missing) < len(missing_best):
                best, missing_best = key, missing
        return (None if missing_best else best), missing_best
