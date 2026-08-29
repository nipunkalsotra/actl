"""§28 P7 instruction 8: Schemathesis contract tests against the running
API's own OpenAPI document (loaded directly from the real ASGI app, no
separate server process needed). Covers every `/agent/v1/*` route --
`POST /agent/v1/messages` (fuzzed input necessarily includes malformed
and unsigned envelopes, proving the endpoint never 500s regardless of
what arrives) plus the P4 `GET /agent/v1/catalog` / `POST /agent/v1/quote`
routes.

Deterministic and network-free: `PAYMENT_PROVIDER` is overridden to the
SimulatorAdapter and Redis/Postgres point at this session's own
testcontainers, matching every other integration test in this repo
(§28 P5/P6/P7) -- LLM_ENABLED is false throughout this whole build's test
suite by construction (P8 hasn't landed yet; there is no Groq call this
phase could possibly make).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
import schemathesis
from redis.asyncio import Redis
from schemathesis.checks import not_a_server_error
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from actl.infrastructure.cache.nonce import NonceCache
from actl.infrastructure.db.uow import UnitOfWork
from actl.infrastructure.providers.simulator.adapter import SimulatorAdapter
from actl.interfaces.http.deps import (
    get_nonce_cache,
    get_payment_provider,
    get_session_factory,
    get_uow,
)
from actl.main import app
from actl.platform.clock import SystemClock


@pytest.fixture
def api_schema(postgres_url: str, redis_url: str) -> Iterator[schemathesis.BaseSchema]:
    # NullPool: schemathesis's ASGI transport may run each generated
    # example's request on its own event loop (visible in the repeated
    # app.startup/app.shutdown lifespan pairs, one per example) --
    # asyncpg connections are strictly single-loop, so any pooled
    # connection reused across two of those loops fails with "another
    # operation is in progress"/"attached to a different loop". A fresh
    # connection per checkout sidesteps that entirely.
    test_engine = create_async_engine(postgres_url, poolclass=NullPool)
    test_session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    test_redis = Redis.from_url(redis_url)
    simulator = SimulatorAdapter(clock=SystemClock())

    async def _override_get_uow() -> AsyncIterator[UnitOfWork]:
        async with UnitOfWork(test_session_factory) as uow:
            yield uow

    # GET /agent/v1/catalog / POST /agent/v1/quote (§28 P4) go through
    # get_uow, not get_session_factory -- both must be overridden, or
    # those routes silently fall back to the real production engine
    # singleton (infrastructure.db.engine.get_session_factory()) instead
    # of this test's own container.
    app.dependency_overrides[get_uow] = _override_get_uow
    app.dependency_overrides[get_session_factory] = lambda: test_session_factory
    app.dependency_overrides[get_nonce_cache] = lambda: NonceCache(test_redis)
    app.dependency_overrides[get_payment_provider] = lambda: simulator
    try:
        yield schemathesis.openapi.from_asgi("/openapi.json", app)
    finally:
        app.dependency_overrides.pop(get_uow, None)
        app.dependency_overrides.pop(get_session_factory, None)
        app.dependency_overrides.pop(get_nonce_cache, None)
        app.dependency_overrides.pop(get_payment_provider, None)


schema = schemathesis.pytest.from_fixture("api_schema")


@schema.include(path_regex=r"^/agent/v1/").parametrize()
def test_agent_v1_routes_never_5xx_on_any_input(case: schemathesis.Case) -> None:
    """The one contract every `/agent/v1/*` route must hold regardless of
    what Hypothesis generates -- including malformed/unsigned envelopes on
    POST /agent/v1/messages, which this necessarily fuzzes into existence:
    a rejection is always a well-formed 4xx/409/503 typed response, never
    an unhandled exception surfacing as 500."""
    case.call_and_validate(checks=(not_a_server_error,))
