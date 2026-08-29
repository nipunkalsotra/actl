"""§14.1 / §18.3 / §28 P7 instruction 3: real-Redis replay protection --
first delivery, sequential duplicate, concurrent duplicate, expired cache,
and Redis-unavailable fails closed. Timestamp-skew and full envelope
replay-rejection (REPLAYED_MESSAGE/CLOCK_SKEW end-to-end) live in
test_replay_and_skew.py; this file is the nonce cache in isolation.
"""

from __future__ import annotations

import asyncio

import pytest
from redis.asyncio import Redis

from actl.infrastructure.cache.nonce import NONCE_TTL_S, NonceCache, NonceCacheUnavailable
from actl.platform.ids import new_id

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_first_delivery_claims_successfully(redis_client: Redis) -> None:
    cache = NonceCache(redis_client)
    msg_id = new_id("msg")

    assert await cache.claim(msg_id) is True


async def test_sequential_duplicate_is_rejected(redis_client: Redis) -> None:
    cache = NonceCache(redis_client)
    msg_id = new_id("msg")

    assert await cache.claim(msg_id) is True
    assert await cache.claim(msg_id) is False
    assert await cache.claim(msg_id) is False  # a third delivery is still a duplicate


async def test_concurrent_duplicate_admits_exactly_one_winner(redis_client: Redis) -> None:
    """Atomic claim semantics: of N truly-concurrent claims for the same
    msg_id, exactly one must win -- never zero, never more than one."""
    cache = NonceCache(redis_client)
    msg_id = new_id("msg")
    n = 25

    results = await asyncio.gather(*(cache.claim(msg_id) for _ in range(n)))

    assert sum(1 for r in results if r) == 1
    assert sum(1 for r in results if not r) == n - 1


async def test_claim_sets_the_exact_ten_minute_ttl(redis_client: Redis) -> None:
    cache = NonceCache(redis_client)
    msg_id = new_id("msg")

    await cache.claim(msg_id)

    ttl = await redis_client.ttl(f"actl:nonce:{msg_id}")
    assert NONCE_TTL_S == 600
    assert 595 <= ttl <= 600


async def test_expired_cache_entry_permits_a_fresh_claim(redis_client: Redis) -> None:
    """Simulates the 10-minute TTL having lapsed by deleting the key
    directly (deterministic, no real waiting) -- proves expiry is what
    actually governs re-claimability, not a hard-coded "always reject"."""
    cache = NonceCache(redis_client)
    msg_id = new_id("msg")

    assert await cache.claim(msg_id) is True
    assert await cache.claim(msg_id) is False

    await redis_client.delete(f"actl:nonce:{msg_id}")

    assert await cache.claim(msg_id) is True


async def test_redis_unavailable_fails_closed() -> None:
    """§14: "do not silently disable replay protection." An unreachable
    Redis must raise, never silently report "not a duplicate"."""
    unreachable = Redis.from_url(
        "redis://127.0.0.1:1/0", socket_connect_timeout=1, socket_timeout=1
    )
    cache = NonceCache(unreachable)

    with pytest.raises(NonceCacheUnavailable):
        await cache.claim(new_id("msg"))

    await unreachable.aclose()
