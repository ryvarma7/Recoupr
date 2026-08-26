"""add llm_cache table

Revision ID: d81c4a7e5f30
Revises: b4efcae13e7b
Create Date: 2026-08-26

"""
from __future__ import annotations

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision = 'd81c4a7e5f30'
down_revision = 'b4efcae13e7b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('llmcacheentry',
    sa.Column('key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('model', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('schema_name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('response_json', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )


def downgrade() -> None:
    op.drop_table('llmcacheentry')
