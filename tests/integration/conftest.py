"""Isolated Postgres and Redis via testcontainers, shared by every
tests/integration/* subdirectory — a real Postgres 16 instance and a real
Redis 7 instance, never a mock or an in-memory substitute. §18.1 is
explicit that Postgres is the system of record and its guarantees
(triggers, checks, FKs) are exactly what these tests exist to prove; §28
P7's replay-protection tests need the exact same real-backend discipline
for Redis's atomic `SET NX EX` (no fake can prove that atomicity).

One container each for the whole test session (spinning either up per
test, or per subdirectory, would dominate runtime); repeatability instead
comes from every test using fresh ULID-based ids/msg_ids, so tests never
collide on shared state regardless of order.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

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


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    with RedisContainer("redis:7-alpine") as container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(container.port)
        yield f"redis://{host}:{port}/0"


@pytest_asyncio.fixture(loop_scope="session")
async def redis_client(redis_url: str) -> AsyncIterator[Redis]:
    # Function-scoped despite the session-scoped container: a fresh
    # connection per test avoids any cross-test event-loop binding issue
    # (the same reasoning as ADR 0005 decision 12 for TestClient's engine),
    # and FLUSHDB keeps nonce keys from one test invisible to the next.
    client = Redis.from_url(redis_url)
    await client.flushdb()
    try:
        yield client
    finally:
        await client.aclose()
