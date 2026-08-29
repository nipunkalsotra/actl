"""§17.3: "SHA-256 of the normalised prompt -> response, 24h TTL." Real
Redis -- a miss returns None, a hit round-trips the exact value, and a
Redis outage fails *open* (a cache miss), never raising into the caller.
"""

from __future__ import annotations

import pytest
from redis.asyncio import Redis

from actl.infrastructure.cache.semantic_cache import SemanticCache

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_miss_returns_none(redis_client: Redis) -> None:
    cache = SemanticCache(redis_client)
    assert await cache.get("no-such-key") is None


async def test_set_then_get_round_trips(redis_client: Redis) -> None:
    cache = SemanticCache(redis_client)
    await cache.set("k1", {"ranking": ["A", "B"], "n": 2})
    assert await cache.get("k1") == {"ranking": ["A", "B"], "n": 2}


async def test_ttl_is_set_on_write(redis_client: Redis) -> None:
    cache = SemanticCache(redis_client, ttl_s=3600)
    await cache.set("k2", {"x": 1})
    ttl = await redis_client.ttl("actl:llm:cache:k2")
    assert 0 < ttl <= 3600


async def test_redis_unavailable_fails_open_not_raising() -> None:
    unreachable = Redis(host="127.0.0.1", port=1, socket_connect_timeout=1)
    cache = SemanticCache(unreachable)
    assert await cache.get("anything") is None
    await cache.set("anything", {"a": 1})  # must not raise
