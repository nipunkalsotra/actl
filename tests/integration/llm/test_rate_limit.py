"""§17.3: the real, atomic, Lua-scripted Redis token bucket -- against a
real container. Covers the corrective instruction's five required
categories: bounded burst consumption, rejection once empty, refill
after injected-clock advancement, concurrent claims admitting no more
than capacity, and a Redis outage falling back safely with zero calls to
Groq.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from redis.asyncio import Redis

from actl.application.ports import LLMUnavailable
from actl.infrastructure.cache.rate_limit import RateLimitUnavailable, TokenBucketLimiter
from actl.infrastructure.cache.semantic_cache import SemanticCache
from actl.infrastructure.llm.groq_client import GroqClient
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import FrozenClock, SystemClock

pytestmark = pytest.mark.asyncio(loop_scope="session")


# ---------------------------------------------------------------------------
# (a) bounded burst consumption
# ---------------------------------------------------------------------------


async def test_bounded_burst_consumption(redis_client: Redis) -> None:
    """A burst of calls, no time elapsed between them, is admitted up to
    exactly the bucket's capacity and no further."""
    clock = FrozenClock(at=SystemClock().now())
    limiter = TokenBucketLimiter(redis_client, name="burst", limit_per_min=5, clock=clock)
    results = [await limiter.try_acquire() for _ in range(8)]
    assert results == [True, True, True, True, True, False, False, False]


async def test_admits_up_to_the_limit_then_denies(redis_client: Redis) -> None:
    clock = FrozenClock(at=SystemClock().now())
    limiter = TokenBucketLimiter(redis_client, name="t1", limit_per_min=3, clock=clock)
    results = [await limiter.try_acquire() for _ in range(4)]
    assert results == [True, True, True, False]


# ---------------------------------------------------------------------------
# (b) rejection after the bucket is empty
# ---------------------------------------------------------------------------


async def test_rejection_after_the_bucket_is_empty(redis_client: Redis) -> None:
    """Once exhausted, every further call is denied -- not just the next
    one -- until real time (via the clock) actually passes."""
    clock = FrozenClock(at=SystemClock().now())
    limiter = TokenBucketLimiter(redis_client, name="empty", limit_per_min=2, clock=clock)
    assert [await limiter.try_acquire() for _ in range(2)] == [True, True]
    assert await limiter.try_acquire() is False
    assert await limiter.try_acquire() is False
    assert await limiter.try_acquire() is False


# ---------------------------------------------------------------------------
# (c) refill after injected-clock advancement
# ---------------------------------------------------------------------------


async def test_refill_after_clock_advancement(redis_client: Redis) -> None:
    """§17.3's token bucket: capacity=limit_per_min, refilling continuously
    at limit_per_min/60 tokens/second -- a partial advance refills a
    partial, proportional number of tokens; a full minute fully refills."""
    clock = FrozenClock(at=SystemClock().now())
    limiter = TokenBucketLimiter(redis_client, name="refill", limit_per_min=6, clock=clock)
    assert [await limiter.try_acquire() for _ in range(6)] == [True] * 6
    assert await limiter.try_acquire() is False

    # 6 tokens/min = 0.1 tokens/sec -- 20s refills exactly 2 tokens.
    clock.advance(timedelta(seconds=20))
    assert [await limiter.try_acquire() for _ in range(2)] == [True, True]
    assert await limiter.try_acquire() is False

    # A full minute from empty fully refills to capacity.
    clock.advance(timedelta(seconds=60))
    assert [await limiter.try_acquire() for _ in range(6)] == [True] * 6
    assert await limiter.try_acquire() is False


async def test_a_new_window_resets_the_count(redis_client: Redis) -> None:
    clock = FrozenClock(at=SystemClock().now())
    limiter = TokenBucketLimiter(redis_client, name="t2", limit_per_min=1, clock=clock)
    assert await limiter.try_acquire() is True
    assert await limiter.try_acquire() is False
    clock.advance(timedelta(seconds=61))
    assert await limiter.try_acquire() is True


async def test_different_names_have_independent_buckets(redis_client: Redis) -> None:
    clock = FrozenClock(at=SystemClock().now())
    a = TokenBucketLimiter(redis_client, name="a", limit_per_min=1, clock=clock)
    b = TokenBucketLimiter(redis_client, name="b", limit_per_min=1, clock=clock)
    assert await a.try_acquire() is True
    assert await b.try_acquire() is True  # not affected by a's usage


# ---------------------------------------------------------------------------
# (d) concurrent claims admit no more than capacity
# ---------------------------------------------------------------------------


async def test_concurrent_claims_admit_no_more_than_capacity(redis_client: Redis) -> None:
    """The whole point of the Lua script: Redis executes it as one atomic
    operation, so many truly-concurrent callers racing the same bucket
    can never together claim more than its capacity -- proven with real
    concurrency (asyncio.gather), not a serial loop."""
    clock = FrozenClock(at=SystemClock().now())
    limiter = TokenBucketLimiter(redis_client, name="concurrent", limit_per_min=10, clock=clock)
    results = await asyncio.gather(*(limiter.try_acquire() for _ in range(50)))
    assert sum(1 for r in results if r) == 10
    assert sum(1 for r in results if not r) == 40


async def test_concurrent_claims_across_independent_redis_connections(redis_url: str) -> None:
    """Same proof, but each caller uses its *own* Redis connection (as
    separate worker processes calling the same GroqClient would) -- the
    atomicity guarantee is Redis-side (the Lua script), not an artifact
    of sharing one Python client's connection pool."""
    clock = FrozenClock(at=SystemClock().now())
    capacity = 8
    name = "concurrent-multi-conn"

    clients = [Redis.from_url(redis_url) for _ in range(30)]
    try:
        limiters = [
            TokenBucketLimiter(c, name=name, limit_per_min=capacity, clock=clock) for c in clients
        ]
        results = await asyncio.gather(*(limiter.try_acquire() for limiter in limiters))
    finally:
        for c in clients:
            await c.aclose()

    assert sum(1 for r in results if r) == capacity


# ---------------------------------------------------------------------------
# (e) Redis outage causes LLM fallback with no Groq call
# ---------------------------------------------------------------------------


async def test_redis_unavailable_fails_closed() -> None:
    clock = FrozenClock(at=SystemClock().now())
    unreachable = Redis(host="127.0.0.1", port=1, socket_connect_timeout=1)
    limiter = TokenBucketLimiter(unreachable, name="t3", limit_per_min=10, clock=clock)
    with pytest.raises(RateLimitUnavailable):
        await limiter.try_acquire()


async def test_redis_outage_falls_back_without_ever_calling_groq(redis_client: Redis) -> None:
    """§28 P8 correction 1: "On Redis rate-limiter unavailability, do not
    call Groq unboundedly: safely skip LLM assistance." Proven at the
    `GroqClient` level: the rate limiter is genuinely unreachable, and the
    underlying Groq SDK call is spied on and asserted to never fire."""
    clock = FrozenClock(at=SystemClock().now())
    unreachable = Redis(host="127.0.0.1", port=1, socket_connect_timeout=1)
    limiter = TokenBucketLimiter(unreachable, name="groq-outage", limit_per_min=10, clock=clock)
    cache = SemanticCache(redis_client)
    breaker = CircuitBreaker(name="groq-outage-test", clock=SystemClock())

    client = GroqClient(
        api_key="gsk_unused_in_this_test",
        model="openai/gpt-oss-120b",
        timeout_s=5,
        breaker=breaker,
        limiter=limiter,
        cache=cache,
    )

    calls = 0

    async def _spy_create(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("Groq must never be called when the rate limiter is unreachable")

    client._client.chat.completions.create = _spy_create  # type: ignore[method-assign]

    with pytest.raises(LLMUnavailable):
        await client.complete_json(
            system="s", user=f"redis-outage-probe-{id(limiter)}", max_tokens=10
        )
    assert calls == 0
