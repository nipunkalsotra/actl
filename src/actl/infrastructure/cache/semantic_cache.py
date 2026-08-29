"""§17.3: "SHA-256 of the normalised prompt -> response, 24h TTL." A
literal exact-match cache keyed by the complete canonical prompt's
sha256 (infrastructure/llm/canonical_prompt.py), not embedding-similarity
search -- matching the architecture's own literal description of what
"semantic cache" means in this build: "repeated demo runs cost
approximately zero calls."

Fails *open* on a Redis error -- the opposite direction from the nonce
cache and rate limiter: a cache is never load-bearing for correctness, so
a Redis outage should degrade to "one extra LLM call" (or, if the LLM is
also down, the existing deterministic fallback), never to a hard failure
of its own.
"""

from __future__ import annotations

import contextlib
import json

from redis.asyncio import Redis
from redis.exceptions import RedisError

_TTL_S_DEFAULT = 86400


def _key(cache_key: str) -> str:
    return f"actl:llm:cache:{cache_key}"


class SemanticCache:
    def __init__(self, client: Redis, *, ttl_s: int = _TTL_S_DEFAULT) -> None:
        self._client = client
        self._ttl_s = ttl_s

    async def get(self, cache_key: str) -> object | None:
        try:
            raw = await self._client.get(_key(cache_key))
        except RedisError:
            return None
        if raw is None:
            return None
        try:
            value: object = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return value

    async def set(self, cache_key: str, value: object) -> None:
        with contextlib.suppress(RedisError):
            await self._client.set(_key(cache_key), json.dumps(value), ex=self._ttl_s)
