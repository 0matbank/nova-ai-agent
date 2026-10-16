"""schedules: one-time, delayed, recurring and condition jobs (plan §39, Phase 19)

Revision ID: 005
Revises: 004
Create Date: 2026-10-16 16:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import core.db.models

revision: str = '005'
down_revision: str | None = '004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('schedules',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('action', sa.String(length=20), nullable=False),
    sa.Column('what', sa.Text(), nullable=False),
    sa.Column('rule', sa.JSON(), nullable=True),
    sa.Column('condition', sa.JSON(), nullable=True),
    sa.Column('next_run_at', core.db.models.UTCDateTime(), nullable=True),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('channel', sa.String(length=20), nullable=False),
    sa.Column('chat_id', sa.String(length=64), nullable=False),
    sa.Column('source', sa.String(length=100), nullable=True),
    sa.Column('request_text', sa.Text(), nullable=True),
    sa.Column('run_count', sa.Integer(), nullable=False),
    sa.Column('last_run_at', core.db.models.UTCDateTime(), nullable=True),
    sa.Column('last_task_id', sa.Integer(), nullable=True),
    sa.Column('created_at', core.db.models.UTCDateTime(), nullable=False),
    sa.Column('updated_at', core.db.models.UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_schedules'))
    )
    with op.batch_alter_table('schedules', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_schedules_enabled'), ['enabled'], unique=False)
        batch_op.create_index(batch_op.f('ix_schedules_next_run_at'), ['next_run_at'],
                              unique=False)
        batch_op.create_index(batch_op.f('ix_schedules_source'), ['source'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('schedules', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_schedules_source'))
        batch_op.drop_index(batch_op.f('ix_schedules_next_run_at'))
        batch_op.drop_index(batch_op.f('ix_schedules_enabled'))
    op.drop_table('schedules')
