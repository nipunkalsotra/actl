"""§17.3: a real Redis-backed token bucket -- atomic via a single Lua
script, so a burst of concurrent callers is serialized by Redis itself
(single-threaded command execution) and can never together claim more
than the bucket's current balance, no matter how many coroutines/
processes race it at once. Replaces an earlier fixed-window
approximation (INCR + EXPIRE), which could admit up to ~2x the intended
ceiling across a single window boundary.

Capacity and refill both derive from the one `LLM_RATE_LIMIT_PER_MIN`
config knob (§17.3: "configured below the published Groq free-tier
ceiling"): capacity = limit_per_min (the maximum burst a caller can
spend at once), refill_per_second = limit_per_min / 60 (so the bucket
fully replenishes from empty over one minute) -- preserving the
"requests per minute" ceiling as both the burst size and the sustained
rate, without adding a second config value.

Fails closed, same posture as the P7 nonce cache
(infrastructure/cache/nonce.py): a Redis error must never be silently
treated as "under the limit." `GroqClient` converts `RateLimitUnavailable`
into `LLMUnavailable`, which every U1/U2/U3 caller already treats as "fall
back to the deterministic path" -- never as "call Groq anyway." A rate
limiter that failed *open* on a Redis outage would turn that outage into
an unbounded retry storm against Groq; failing closed means the opposite:
zero further Groq calls until Redis recovers, money flow unaffected
either way since the deterministic fallback never depends on the LLM.
"""

from __future__ import annotations

from redis.asyncio import Redis
from redis.exceptions import RedisError

from actl.platform.clock import Clock

# >= 2x the time to fully refill from empty (60s, by construction --
# capacity / (capacity / 60) is always 60 regardless of capacity), so a
# bucket key never expires mid-use but doesn't linger forever after a
# quiet period either.
_TTL_S = 120

# Atomic lazy-refill token bucket. KEYS[1] = bucket key (a Redis HASH with
# fields "tokens" and "ts"). ARGV: capacity, refill_per_second, now
# (seconds, float, from the injected Clock -- never Redis's own TIME, so
# callers/tests control it exactly), requested tokens, ttl_s.
# Returns {allowed (0/1), tokens_remaining} -- a single EVAL/EVALSHA call,
# so the read-refill-check-write sequence is one atomic Redis operation.
_TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_per_second = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])
local ttl_s = tonumber(ARGV[5])

local raw = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(raw[1])
local last_ts = tonumber(raw[2])

if tokens == nil then
  tokens = capacity
  last_ts = now
end

local elapsed = now - last_ts
if elapsed < 0 then
  elapsed = 0
end
tokens = math.min(capacity, tokens + elapsed * refill_per_second)

-- A small epsilon absorbs float round-trip noise: every argument here
-- crosses the Redis text protocol as a string, so a value that is
-- mathematically exactly `requested` (e.g. one full refill period later)
-- can arrive a few ULPs under it after two string<->float round trips.
local epsilon = 0.0000001
local allowed = 0
if tokens + epsilon >= requested then
  tokens = tokens - requested
  allowed = 1
end

-- tostring() on a Lua number truncates precision (observed: a stored
-- timestamp like 1788006200.824076 round-trips as 1788006200.8241) --
-- over many calls that truncation compounds into a multi-microsecond
-- timestamp error, large enough to make an exact-refill-boundary claim
-- flake. %.17g preserves full double precision through the string
-- round trip Redis's text protocol always requires.
local tokens_str = string.format('%.17g', tokens)
local now_str = string.format('%.17g', now)
redis.call('HMSET', key, 'tokens', tokens_str, 'ts', now_str)
redis.call('EXPIRE', key, ttl_s)

return {allowed, tokens_str}
"""


class RateLimitUnavailable(Exception):
    """Redis could not be reached to check/claim tokens. Callers must
    treat this as rate-limited (fail closed), never as "under the
    limit" -- the same posture NonceCacheUnavailable takes for replay
    protection (§28 P7 instruction 3)."""


def _key(name: str) -> str:
    return f"actl:ratelimit:{name}"


class TokenBucketLimiter:
    """A real, atomic, lazy-refill token bucket. Time comes from an
    injected Clock (never Redis's own TIME command or the wall clock), so
    refill behaviour is deterministically testable with FrozenClock,
    matching every other platform component's own convention."""

    def __init__(self, client: Redis, *, name: str, limit_per_min: int, clock: Clock) -> None:
        self._client = client
        self._key = _key(name)
        self._capacity = limit_per_min
        self._refill_per_second = limit_per_min / 60.0
        self._clock = clock
        self._script = client.register_script(_TOKEN_BUCKET_LUA)

    async def try_acquire(self, tokens: int = 1) -> bool:
        """Returns True if `tokens` were claimed from the bucket's current
        balance, False if the balance right now is insufficient. Raises
        `RateLimitUnavailable` if Redis cannot be reached -- never
        silently treated as "under the limit.\""""
        now = self._clock.now().timestamp()
        try:
            result = await self._script(
                keys=[self._key],
                args=[self._capacity, self._refill_per_second, now, tokens, _TTL_S],
            )
        except RedisError as exc:
            raise RateLimitUnavailable(str(exc)) from exc
        allowed, _remaining = result
        return bool(int(allowed))
