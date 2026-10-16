"""project memory: what kind of entry + which task wrote it (plan §20, §51, Phase 17)

Revision ID: 004
Revises: 003
Create Date: 2026-10-16 12:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '004'
down_revision: str | None = '003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('memories', schema=None) as batch_op:
        batch_op.add_column(sa.Column('kind', sa.String(length=30), nullable=False,
                                      server_default='note'))
        batch_op.add_column(sa.Column('task_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_memories_kind'), ['kind'], unique=False)
        batch_op.create_index(batch_op.f('ix_memories_task_id'), ['task_id'], unique=False)
        batch_op.create_foreign_key(batch_op.f('fk_memories_task_id_tasks'), 'tasks',
                                    ['task_id'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table('memories', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_memories_task_id_tasks'), type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_memories_task_id'))
        batch_op.drop_index(batch_op.f('ix_memories_kind'))
        batch_op.drop_column('task_id')
        batch_op.drop_column('kind')
