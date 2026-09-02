"""FastAPI dependencies. Nothing here reads os.environ or calls
datetime.now() directly (§26) -- routers get a UnitOfWork and a Clock
through these, never build either themselves.

`get_payment_provider` reads `request.app.state.payment_provider` rather
than constructing an adapter itself: `actl.interfaces` is one of the
import-linter contract's forbidden `source_modules` for reaching
`infrastructure.providers.razorpay` (§28 P5) — the concrete adapter is
built once in `actl.main`'s lifespan (outside that contract's scope) and
handed down, the same pattern `app.state.engine` already uses."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast

from fastapi import Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application.ports import LLMClient, PaymentProvider
from actl.infrastructure.cache.nonce import NonceCache
from actl.infrastructure.db.engine import get_session_factory as _get_session_factory
from actl.infrastructure.db.uow import UnitOfWork
from actl.infrastructure.llm.health import LLMHealth
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import Clock, SystemClock

_clock: Clock = SystemClock()


async def get_uow() -> AsyncIterator[UnitOfWork]:
    async with UnitOfWork() as uow:
        yield uow


def get_clock() -> Clock:
    return _clock


def get_payment_provider(request: Request) -> PaymentProvider:
    return cast(PaymentProvider, request.app.state.payment_provider)


def get_llm_client(request: Request) -> LLMClient:
    return cast(LLMClient, request.app.state.llm_client)


def get_llm_health(request: Request) -> LLMHealth:
    return cast(LLMHealth, request.app.state.llm_health)


def get_redis(request: Request) -> Redis:
    return cast(Redis, request.app.state.redis_client)


def get_nonce_cache(request: Request) -> NonceCache:
    return NonceCache(get_redis(request))


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return _get_session_factory()


def get_breaker(request: Request) -> CircuitBreaker:
    return cast(CircuitBreaker, request.app.state.breaker)
