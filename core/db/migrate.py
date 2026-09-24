"""Startup schema check + migration (plan §21A).

- Current schema version is checked before the agent starts.
- An existing database is always backed up before any migration runs.
- If a migration fails, the backup is restored (automatic rollback) and
  MigrationError is raised so the service can enter safe mode + alert.
- A database whose revision this code does not know (e.g. newer than the
  code after a rollback) is never touched.
"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

from core.db.engine import make_engine
from core.log import get_logger

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
_log = get_logger("core")


class MigrationError(Exception):
    def __init__(self, message: str, rolled_back: bool = False) -> None:
        super().__init__(message)
        self.rolled_back = rolled_back


@dataclass(frozen=True)
class MigrationResult:
    engine: Engine
    before: str | None
    after: str
    backup: Path | None


def alembic_config() -> Config:
    cfg = Config(str(MIGRATIONS_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    return cfg


def head_revision() -> str:
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    assert head is not None
    return head


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def backup_database(db_path: Path, backups_dir: Path, label: str) -> Path:
    """Consistent online copy via the SQLite backup API."""
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    dst_path = backups_dir / f"agent-{stamp}-{label}.db"
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(dst_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return dst_path


def _restore(backup: Path, db_path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)
    shutil.copy2(backup, db_path)


def upgrade(engine: Engine, revision: str = "head") -> None:
    cfg = alembic_config()
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, revision)


def check_and_migrate(db_path: Path, backups_dir: Path) -> MigrationResult:
    head = head_revision()
    fresh = not db_path.exists()
    engine = make_engine(db_path)
    try:
        before = current_revision(engine)
    except Exception as e:
        engine.dispose()
        raise MigrationError(f"cannot read schema version: {type(e).__name__}: {e}") from e

    known = {s.revision for s in ScriptDirectory.from_config(alembic_config()).walk_revisions()}
    if before is not None and before not in known:
        engine.dispose()
        raise MigrationError(
            f"database schema {before!r} is unknown to this code (head {head!r}); "
            "refusing to touch it"
        )
    if before == head:
        return MigrationResult(engine, before, head, None)

    backup = None
    if not fresh:
        backup = backup_database(db_path, backups_dir, f"from-{before or 'none'}")
        _log.info(f"database backed up before migration: {backup.name}",
                  extra={"action": "db.backup", "status": "ok"})
    try:
        upgrade(engine)
    except Exception as e:
        engine.dispose()
        rolled_back = False
        if backup is not None:
            _restore(backup, db_path)
            rolled_back = True
        elif fresh:
            db_path.unlink(missing_ok=True)
        raise MigrationError(
            f"migration {before} -> {head} failed: {type(e).__name__}: {e}", rolled_back
        ) from e

    _log.info(f"database migrated {before} -> {head}",
              extra={"action": "db.migrate", "status": "ok"})
    return MigrationResult(engine, before, head, backup)
