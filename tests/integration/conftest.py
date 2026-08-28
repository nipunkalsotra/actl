"""Isolated Postgres via testcontainers, shared by every tests/integration/*
subdirectory — a real Postgres 16 instance, real triggers, real constraints.
Never substitute SQLite or a mock to make this easier; §18.1 is explicit
that Postgres is the system of record and its guarantees (triggers, checks,
FKs) are exactly what these tests exist to prove.

One container for the whole test session (spinning up Postgres per test, or
per subdirectory, would dominate runtime); repeatability instead comes from
every test using fresh ULID-based ids, so tests never collide on shared
rows regardless of order.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.community.postgres import PostgresContainer

# Docker Desktop's socket isn't shared with the Ryuk cleanup sidecar in this
# environment; we stop the container explicitly via the context manager
# below regardless, so disabling Ryuk costs nothing but auto-cleanup of
# orphans from a killed test run.
os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    # Default max_connections (100) is too small for a genuine 200-way
    # concurrent-append test once the engine's own pool is added on top;
    # raised here rather than artificially throttling the pool, so
    # "200 parallel appends" means 200 real concurrent transactions.
    container = PostgresContainer("postgres:16", driver="asyncpg")
    container.with_command("postgres -c max_connections=300")
    with container:
        url = container.get_connection_url()
        env = {**os.environ, "DATABASE_URL": url}
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=REPO_ROOT,
            env=env,
            check=True,
        )
        yield url


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def engine(postgres_url: str) -> AsyncIterator[AsyncEngine]:
    # Generous pool: tests/integration/audit's 200-parallel-append test needs
    # real concurrent connections, not a queue in front of a small pool.
    eng = create_async_engine(postgres_url, pool_size=50, max_overflow=200)
    yield eng
    await eng.dispose()


@pytest.fixture(scope="session")
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
