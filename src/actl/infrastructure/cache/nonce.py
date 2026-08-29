"""§14.1 / §18.3: Redis replay-nonce cache keyed by msg_id
(`actl:nonce:{msg_id}`, exact 10-minute TTL). Atomic claim via `SET ... NX
EX` -- Redis serialises the command server-side, so two concurrent claims
for the same msg_id can never both succeed (§28 P7 instruction 3).

Fails closed: a Redis connection/timeout error surfaces as
`NonceCacheUnavailable` rather than being swallowed as "not a duplicate" --
§14 requires replay protection never be silently disabled.
"""

from __future__ import annotations

from redis.asyncio import Redis
from redis.exceptions import RedisError

NONCE_TTL_S = 600  # exact 10 minutes, §14.1
_CLAIMED = "1"


class NonceCacheUnavailable(Exception):
    """Redis could not be reached to check/claim a msg_id. Callers must
    treat this as "reject the message," never as "not a duplicate."""


def _key(msg_id: str) -> str:
    return f"actl:nonce:{msg_id}"


class NonceCache:
    def __init__(self, client: Redis, *, ttl_s: int = NONCE_TTL_S) -> None:
        self._client = client
        self._ttl_s = ttl_s

    async def claim(self, msg_id: str) -> bool:
        """Returns True on first delivery (this call's claim won), False
        if `msg_id` was already claimed (a duplicate/replay). Raises
        `NonceCacheUnavailable` if Redis cannot be reached."""
        try:
            won = await self._client.set(_key(msg_id), _CLAIMED, nx=True, ex=self._ttl_s)
        except RedisError as exc:
            raise NonceCacheUnavailable(str(exc)) from exc
        return bool(won)
