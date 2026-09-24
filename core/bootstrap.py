"""Startup: validate config → create runtime dirs → load secrets → logging.

    uv run python -m core.bootstrap --check

Exit codes: 0 = valid startup, 2 = invalid config (agent refuses to start).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from core.config import AppConfig, ConfigError, Secrets, load_config, load_secrets
from core.log import get_logger, setup_logging

EXIT_OK = 0
EXIT_INVALID_CONFIG = 2

RUNTIME_DIRS = (
    "data_dir", "secrets_dir", "sessions_dir", "workspace_dir",
    "downloads_dir", "logs_dir", "backups_dir", "local_models_dir",
)


@dataclass(frozen=True)
class AppContext:
    config: AppConfig
    secrets: Secrets


def bootstrap(
    worker: str = "core",
    config_dir: Path | None = None,
    root: Path | None = None,
    console_logs: bool = True,
) -> AppContext:
    """Raises ConfigError on any invalid configuration."""
    cfg = load_config(config_dir, root)
    for name in RUNTIME_DIRS:
        cfg.path(name).mkdir(parents=True, exist_ok=True)
    secrets = load_secrets(cfg.path("secrets_dir"))
    log = cfg.default.logging
    setup_logging(
        cfg.path("logs_dir"), log.categories, worker=worker, level=log.level,
        max_bytes=log.rotation.max_bytes, backup_count=log.rotation.backup_count,
        known_secrets=secrets.raw_values(), console=console_logs,
    )
    get_logger("core").info(
        "startup config valid", extra={"action": "startup", "status": "ok"}
    )
    return AppContext(cfg, secrets)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Personal Agent startup check")
    parser.add_argument("--check", action="store_true", help="validate and exit")
    parser.add_argument("--config-dir", type=Path)
    parser.add_argument("--root", type=Path)
    args = parser.parse_args(argv)

    try:
        ctx = bootstrap(config_dir=args.config_dir, root=args.root, console_logs=False)
    except ConfigError as e:
        print(f"STARTUP REFUSED - {e}", file=sys.stderr)
        return EXIT_INVALID_CONFIG

    cfg = ctx.config
    print("STARTUP OK")
    print(f"  root:      {cfg.root}")
    print(f"  logs:      {cfg.path('logs_dir')}")
    providers = ", ".join(f"{k}={v.enabled}" for k, v in cfg.providers.providers.items())
    print(f"  providers: {providers}")
    print(f"  projects:  {', '.join(cfg.projects) or '-'}")
    print(f"  secrets:   {len(ctx.secrets.keys())} key(s) loaded (values hidden)")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
