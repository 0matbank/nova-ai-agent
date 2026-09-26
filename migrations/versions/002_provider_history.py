"""provider history + circuit breaker state (plan §8.6, §8.9)

Revision ID: 002
Revises: 001
Create Date: 2026-09-26 16:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import core.db.models

revision: str = '002'
down_revision: str | None = '001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('provider_calls',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('ts', core.db.models.UTCDateTime(), nullable=False),
    sa.Column('provider', sa.String(length=50), nullable=False),
    sa.Column('task_type', sa.String(length=50), nullable=False),
    sa.Column('task_id', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('error_category', sa.String(length=30), nullable=True),
    sa.Column('duration_ms', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_provider_calls'))
    )
    with op.batch_alter_table('provider_calls', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_provider_calls_provider'), ['provider'], unique=False)
        batch_op.create_index(batch_op.f('ix_provider_calls_ts'), ['ts'], unique=False)

    op.create_table('provider_state',
    sa.Column('provider', sa.String(length=50), nullable=False),
    sa.Column('consecutive_failures', sa.Integer(), nullable=False),
    sa.Column('trips', sa.Integer(), nullable=False),
    sa.Column('cooldown_until', core.db.models.UTCDateTime(), nullable=True),
    sa.Column('reason', sa.String(length=300), nullable=True),
    sa.Column('updated_at', core.db.models.UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('provider', name=op.f('pk_provider_state'))
    )


def downgrade() -> None:
    op.drop_table('provider_state')
    with op.batch_alter_table('provider_calls', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_provider_calls_ts'))
        batch_op.drop_index(batch_op.f('ix_provider_calls_provider'))
    op.drop_table('provider_calls')
