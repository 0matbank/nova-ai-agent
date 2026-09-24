"""Load + validate all config files. Any problem raises ConfigError (plan §17G:
"Invalid config হলে Agent unsafe mode-এ start করবে না")."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from core.config.schema import (
    AgentsConfig,
    AppConfig,
    DefaultConfig,
    ModelsConfig,
    PermissionsConfig,
    ProjectProfile,
    ProvidersConfig,
    ResourcesConfig,
    SchedulesConfig,
    SkillsConfig,
    WorkersConfig,
)

APP_DIR = Path(__file__).resolve().parents[2]
ROOT_ENV_VAR = "PERSONAL_AGENT_ROOT"

FILES: dict[str, type[BaseModel]] = {
    "default": DefaultConfig,
    "providers": ProvidersConfig,
    "models": ModelsConfig,
    "agents": AgentsConfig,
    "skills": SkillsConfig,
    "permissions": PermissionsConfig,
    "workers": WorkersConfig,
    "resources": ResourcesConfig,
    "schedules": SchedulesConfig,
}

RUNTIME_PATHS = (
    "data_dir", "database", "secrets_dir", "sessions_dir", "workspace_dir",
    "downloads_dir", "logs_dir", "backups_dir", "local_models_dir",
)


class ConfigError(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("invalid configuration:\n" + "\n".join(f"  - {e}" for e in errors))


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate keys instead of silently overwriting."""


def _construct_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    seen: set[Any] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key {key!r}", key_node.start_mark
            )
        seen.add(key)
    return loader.construct_mapping(node)


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)


def _read_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return yaml.load(fh, Loader=_UniqueKeyLoader)  # noqa: S506 - SafeLoader subclass


def _format(file: str, err: ValidationError) -> list[str]:
    out = []
    for e in err.errors():
        loc = ".".join(str(p) for p in e["loc"]) or "<root>"
        out.append(f"{file}: {loc}: {e['msg']}")
    return out


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def load_config(config_dir: Path | None = None, root: Path | None = None) -> AppConfig:
    config_dir = config_dir or APP_DIR / "config"
    errors: list[str] = []
    parsed: dict[str, Any] = {}

    for name, model in FILES.items():
        path = config_dir / f"{name}.yaml"
        if not path.is_file():
            errors.append(f"{name}.yaml: file missing")
            continue
        try:
            parsed[name] = model.model_validate(_read_yaml(path))
        except yaml.YAMLError as e:
            errors.append(f"{name}.yaml: YAML error: {e}")
        except ValidationError as e:
            errors.extend(_format(f"{name}.yaml", e))

    projects: dict[str, ProjectProfile] = {}
    for path in sorted((config_dir / "projects").glob("*.yaml")):
        try:
            projects[path.stem] = ProjectProfile.model_validate(_read_yaml(path))
        except yaml.YAMLError as e:
            errors.append(f"projects/{path.name}: YAML error: {e}")
        except ValidationError as e:
            errors.extend(_format(f"projects/{path.name}", e))

    # Cross-file: every provider's alias set must exist in models.yaml.
    if "providers" in parsed and "models" in parsed:
        sets = parsed["models"].alias_sets
        for pname, p in parsed["providers"].providers.items():
            if p.model_alias_set not in sets:
                errors.append(
                    f"providers.yaml: {pname}.model_alias_set '{p.model_alias_set}' "
                    "not defined in models.yaml"
                )

    if errors:
        raise ConfigError(errors)

    default: DefaultConfig = parsed["default"]
    env_root = os.environ.get(ROOT_ENV_VAR)
    resolved_root = (root or (Path(env_root) if env_root else None)
                     or default.paths.root or APP_DIR.parent).resolve()

    cfg = AppConfig(
        app_dir=APP_DIR, root=resolved_root, projects=projects, **parsed,
    )

    # Secrets boundary (plan §22/§23): runtime data must never sit inside the repo.
    for name in RUNTIME_PATHS:
        if _is_within(cfg.path(name), APP_DIR):
            errors.append(f"default.yaml: paths.{name} resolves inside the repo ({cfg.path(name)})")
    if errors:
        raise ConfigError(errors)
    return cfg
