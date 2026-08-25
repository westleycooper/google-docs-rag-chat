"""Alembic environment.

Runs migrations synchronously (psycopg) even though the application uses asyncpg.
Migrations are a deployment-time, single-threaded concern, and the sync path is
markedly simpler to reason about when one is failing at 3am.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from ragoogle_infra.persistence.schema import METADATA

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = METADATA


def _url() -> str:
    url = os.environ.get("RAGOOGLE_DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("set RAGOOGLE_DATABASE_URL or sqlalchemy.url")
    # The app config carries one URL for both paths. Migrations run on psycopg3
    # (sync); the application runs on asyncpg. Normalise either spelling -- and
    # a bare "postgresql://", which SQLAlchemy would otherwise resolve to
    # psycopg2 -- onto the sync driver this project actually installs.
    for prefix in ("postgresql+asyncpg://", "postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
