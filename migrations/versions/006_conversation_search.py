"""conversation search: SQLite FTS5 over the owner's requests and Nova's answers (plan
§65 V1.3-2). Trigram tokens — Bangla has no word stems to cut, so «পিসি» also finds
«পিসির». Kept in step with `tasks` by triggers; filled from the tasks already there.

Revision ID: 006
Revises: 005
Create Date: 2026-10-26 18:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = '006'
down_revision: str | None = '005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE VIRTUAL TABLE tasks_fts USING fts5(request_text, result_summary, "
               "content='tasks', content_rowid='id', tokenize='trigram')")
    op.execute("CREATE TRIGGER tasks_fts_insert AFTER INSERT ON tasks BEGIN "
               "INSERT INTO tasks_fts(rowid, request_text, result_summary) "
               "VALUES (new.id, new.request_text, new.result_summary); END")
    op.execute("CREATE TRIGGER tasks_fts_delete AFTER DELETE ON tasks BEGIN "
               "INSERT INTO tasks_fts(tasks_fts, rowid, request_text, result_summary) "
               "VALUES ('delete', old.id, old.request_text, old.result_summary); END")
    op.execute("CREATE TRIGGER tasks_fts_update AFTER UPDATE OF request_text, "
               "result_summary ON tasks BEGIN "
               "INSERT INTO tasks_fts(tasks_fts, rowid, request_text, result_summary) "
               "VALUES ('delete', old.id, old.request_text, old.result_summary); "
               "INSERT INTO tasks_fts(rowid, request_text, result_summary) "
               "VALUES (new.id, new.request_text, new.result_summary); END")
    op.execute("INSERT INTO tasks_fts(tasks_fts) VALUES ('rebuild')")


def downgrade() -> None:
    for trigger in ("tasks_fts_insert", "tasks_fts_delete", "tasks_fts_update"):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    op.execute("DROP TABLE IF EXISTS tasks_fts")
