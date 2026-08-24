"""Alembic environment — targets the live SQLModel metadata, both dialects.

The URL resolution order: RECOUPR_DATABASE_URL → DATABASE_URL → alembic.ini
placeholder. Postgres deployments run `alembic upgrade head`; local SQLite dev
gets `SQLModel.metadata.create_all` from app startup and never needs this.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make the app package importable when alembic runs from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlmodel import SQLModel

from app.models.entities import *

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata

_url = (
    os.environ.get("RECOUPR_DATABASE_URL")
    or os.environ.get("DATABASE_URL")
    or config.get_main_option("sqlalchemy.url")
)
# Normalize SQLAlchemy 2.x postgres dialect names for both driver families.
if _url.startswith("postgres://"):
    _url = _url.replace("postgres://", "postgresql://", 1)
config.set_main_option("sqlalchemy.url", _url)


def run_migrations_offline() -> None:
    context.configure(
        url=_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=_url.startswith("sqlite"),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=_url.startswith("sqlite"),
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
