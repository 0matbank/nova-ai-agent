"""idempotency ledger for external side effects (plan §9.1)

Revision ID: 003
Revises: 002
Create Date: 2026-09-26 16:00:01
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import core.db.models

revision: str = '003'
down_revision: str | None = '002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('side_effects',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('task_id', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('action', sa.String(length=100), nullable=False),
    sa.Column('target', sa.String(length=300), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('created_at', core.db.models.UTCDateTime(), nullable=False),
    sa.Column('completed_at', core.db.models.UTCDateTime(), nullable=True),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], name=op.f('fk_side_effects_task_id_tasks')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_side_effects')),
    sa.UniqueConstraint('task_id', 'key', name=op.f('uq_side_effects_task_id'))
    )
    with op.batch_alter_table('side_effects', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_side_effects_task_id'), ['task_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('side_effects', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_side_effects_task_id'))
    op.drop_table('side_effects')
