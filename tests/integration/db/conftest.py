"""Isolated Postgres via testcontainers (§28 P2: testcontainers-backed
integration tests) — a real Postgres 16 instance, real triggers, real
constraints. Never substitute SQLite or a mock to make this easier; §18.1
is explicit that Postgres is the system of record and its guarantees
(triggers, checks, FKs) are exactly what these tests exist to prove.

One container for the whole test session (spinning up Postgres per test
would dominate runtime); repeatability instead comes from every test using
fresh ULID-based ids, so tests never collide on shared rows regardless of
order.
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

from actl.domain.mandate.hashing import compute_spec_hash
from actl.domain.mandate.models import (
    Delegate,
    Mandate,
    MandateBounds,
    MandateControls,
    MandateIntent,
    MandateSignature,
    MandateTemporal,
    Principal,
)
from actl.domain.mandate.signing import sign_spec_hash
from actl.platform.ids import new_id

_TEST_SIGNING_KEY = b"integration-test-signing-key"

# Docker Desktop's socket isn't shared with the Ryuk cleanup sidecar in this
# environment; we stop the container explicitly via the context manager
# below regardless, so disabling Ryuk costs nothing but auto-cleanup of
# orphans from a killed test run.
os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("postgres:16", driver="asyncpg") as container:
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
    eng = create_async_engine(postgres_url)
    yield eng
    await eng.dispose()


@pytest.fixture(scope="session")
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


def make_locked_mandate() -> Mandate:
    """A fresh, uniquely-ided, locked Mandate for one test — fresh ids keep
    tests repeatable without needing per-test database isolation."""
    draft = Mandate(
        mandate_id=new_id("mdt"),
        version=1,
        principal=Principal(type="human", id="usr_test"),
        delegate=Delegate(type="agent", id="agt_test", key_id="ed25519:test"),
        intent=MandateIntent(
            category="travel.hotel", location="Goa, IN", check_in="2026-09-12", nights=3, rooms=1
        ),
        bounds=MandateBounds(
            currency="INR",
            max_total_minor=900000,
            max_unit_minor=300000,
            max_transactions=1,
            allowed_categories=["travel.hotel"],
            blocked_merchants=[],
            require_refundable=True,
            max_price_delta_bps=0,
        ),
        temporal=MandateTemporal(
            not_before="2026-01-01T00:00:00.000Z",
            expires_at="2027-01-01T00:00:00.000Z",
            quote_ttl_s=120,
        ),
        controls=MandateControls(human_confirm_required=True, revocable=True),
    )
    spec_hash = compute_spec_hash(draft)
    signature = MandateSignature(
        alg="HMAC-SHA256", key_id="mk_1", value=sign_spec_hash(spec_hash, _TEST_SIGNING_KEY)
    )
    return draft.model_copy(update={"spec_hash": spec_hash, "signature": signature})
