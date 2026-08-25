"""Async engine and session factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine


def make_engine(url: str, *, echo: bool = False, pool_size: int = 10) -> AsyncEngine:
    """Build the application engine.

    Normalises the URL onto asyncpg so one `RAGOOGLE_DATABASE_URL` can serve both
    the app and Alembic, which runs on psycopg3 (see migrations/env.py).
    """
    for prefix in ("postgresql+psycopg://", "postgresql://", "postgres://"):
        if url.startswith(prefix):
            url = "postgresql+asyncpg://" + url[len(prefix) :]
            break
    return create_async_engine(
        url,
        echo=echo,
        pool_size=pool_size,
        max_overflow=pool_size,
        pool_pre_ping=True,  # a recycled connection killed by a proxy is common
    )


@asynccontextmanager
async def transaction(engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """A connection inside a transaction, committed on clean exit."""
    async with engine.begin() as conn:
        yield conn
