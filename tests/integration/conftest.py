"""Integration fixtures.

These tests need a real Postgres with pgvector, because what they verify -- an
HNSW index, a tsvector trigger, CHECK constraints, ON DELETE CASCADE -- does not
exist anywhere else. A fake would be asserting that our mock behaves like our
mock.

They skip cleanly when no database is configured, so a clean checkout still runs
the full unit suite:

    docker compose up -d postgres
    RAGOOGLE_TEST_DATABASE_URL=postgresql://ragoogle:ragoogle@localhost:5433/ragoogle \
      uv run pytest tests/integration
"""

from __future__ import annotations

import os

import pytest

URL = os.environ.get("RAGOOGLE_TEST_DATABASE_URL")

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def dsn() -> str:
    if not URL:
        pytest.skip("set RAGOOGLE_TEST_DATABASE_URL to run integration tests")
    return URL.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
def conn(dsn: str):
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(dsn, autocommit=False) as connection:
        yield connection
        # Every test rolls back, so the schema under test is never mutated by
        # the act of testing it.
        connection.rollback()


@pytest.fixture
def vector_literal():
    def build(seed: int, dimensions: int = 1024) -> str:
        return "[" + ",".join(str(((i * seed) % 97) / 97) for i in range(dimensions)) + "]"

    return build
