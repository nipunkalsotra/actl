"""A dedicated, isolated Postgres for tests/golden only -- never shared
with tests/integration or tests/chaos's own session-scoped container.
§28 P9 instruction 5's "stable across reruns" claim only holds if audit
seq numbers start at 1 every time this file's tests run, which requires
a container nothing else has written to -- sharing the wider integration
suite's container (as tests/chaos does, accepting its "start_seq" offset
workaround) would make the demo scenarios' own golden fixtures compare
against an unpredictable starting seq instead.
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

os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    container = PostgresContainer("postgres:16", driver="asyncpg")
    with container:
        url = container.get_connection_url()
        env = {**os.environ, "DATABASE_URL": url}
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=REPO_ROOT, env=env, check=True,
        )
        yield url


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def engine(postgres_url: str) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(postgres_url)
    yield eng
    await eng.dispose()


@pytest.fixture(scope="session")
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
