"""Phase 3: SQLite + SQLAlchemy + Alembic (plan §21A)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect

from core.db import migrate
from core.db.migrate import MigrationError, check_and_migrate, head_revision
from core.db.models import Base

TABLES = {"tasks", "task_steps", "messages", "projects", "memories", "skills", "agents",
          "approvals", "audit_log", "files_index", "settings"}


def test_fresh_database_created_at_head(tmp_path: Path) -> None:
    res = check_and_migrate(tmp_path / "agent.db", tmp_path / "bk")
    assert res.before is None and res.after == head_revision() == "001"
    assert res.backup is None                       # nothing to back up yet
    assert set(inspect(res.engine).get_table_names()) >= TABLES
    res.engine.dispose()


def test_models_match_migrations(tmp_path: Path) -> None:
    res = check_and_migrate(tmp_path / "agent.db", tmp_path / "bk")
    with res.engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    res.engine.dispose()
    assert diff == [], f"models drifted from migrations — add a new revision: {diff}"


def test_at_head_no_backup(tmp_path: Path) -> None:
    check_and_migrate(tmp_path / "agent.db", tmp_path / "bk").engine.dispose()
    res = check_and_migrate(tmp_path / "agent.db", tmp_path / "bk")
    assert res.backup is None and res.before == res.after
    res.engine.dispose()


def _legacy_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE marker (v TEXT)")
    conn.execute("INSERT INTO marker VALUES ('keep-me')")
    conn.commit()
    conn.close()


def test_existing_db_backed_up_before_migration(tmp_path: Path) -> None:
    db = tmp_path / "agent.db"
    _legacy_db(db)
    res = check_and_migrate(db, tmp_path / "bk")
    res.engine.dispose()
    assert res.backup is not None and res.backup.exists()
    rows = sqlite3.connect(res.backup).execute("SELECT v FROM marker").fetchall()
    assert rows == [("keep-me",)]


def test_failed_migration_rolls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "agent.db"
    _legacy_db(db)

    def broken_upgrade(engine, revision="head"):  # type: ignore[no-untyped-def]
        with engine.begin() as conn:
            conn.exec_driver_sql("DROP TABLE marker")
        raise RuntimeError("boom in migration")

    monkeypatch.setattr(migrate, "upgrade", broken_upgrade)
    with pytest.raises(MigrationError) as ei:
        check_and_migrate(db, tmp_path / "bk")
    assert ei.value.rolled_back
    assert sqlite3.connect(db).execute("SELECT v FROM marker").fetchall() == [("keep-me",)]


def test_unknown_revision_refused(tmp_path: Path) -> None:
    db = tmp_path / "agent.db"
    check_and_migrate(db, tmp_path / "bk").engine.dispose()
    conn = sqlite3.connect(db)
    conn.execute("UPDATE alembic_version SET version_num='999_future'")
    conn.commit()
    conn.close()
    with pytest.raises(MigrationError, match="unknown to this code"):
        check_and_migrate(db, tmp_path / "bk")
    assert not (tmp_path / "bk").exists()          # untouched: no backup, no upgrade


def test_foreign_keys_enforced(tmp_path: Path) -> None:
    res = check_and_migrate(tmp_path / "agent.db", tmp_path / "bk")
    with res.engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
    res.engine.dispose()
