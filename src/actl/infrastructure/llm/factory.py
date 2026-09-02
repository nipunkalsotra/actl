"""Picks NullLLMClient / ReplayLLMClient / GroqClient from settings (§28
P8), mirroring `infrastructure/providers/factory.py`'s own precedent
(§28 P5) exactly: called only from `actl.main`/`actl.cli`/`actl.worker`,
never from `actl.application`/`actl.interfaces`, which receive the
constructed `LLMClient` as a parameter instead.

Precedence matches §28 P8 instruction 1's "LLM_ENABLED=false must
completely disable Groq calls" literally: LLM_ENABLED is checked first,
before DEMO_REPLAY -- disabled means disabled, regardless of what else is
configured.
"""

from __future__ import annotations

from pathlib import Path

from redis.asyncio import Redis

from actl.application.ports import LLMClient
from actl.config import Settings
from actl.infrastructure.cache.rate_limit import TokenBucketLimiter
from actl.infrastructure.cache.semantic_cache import SemanticCache
from actl.infrastructure.llm.fallback import NullLLMClient
from actl.infrastructure.llm.groq_client import GroqClient
from actl.infrastructure.llm.health import LLMHealth
from actl.infrastructure.llm.replay_client import ReplayLLMClient
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import Clock

DEFAULT_CASSETTE_DIR = Path(__file__).resolve().parents[4] / "fixtures" / "llm_cassettes"


def build_llm_client(
    settings: Settings,
    *,
    redis_client: Redis,
    breaker: CircuitBreaker,
    clock: Clock,
    cassette_dir: Path = DEFAULT_CASSETTE_DIR,
    health: LLMHealth | None = None,
) -> LLMClient:
    if not settings.llm_enabled:
        return NullLLMClient()
    if settings.demo_replay:
        return ReplayLLMClient(cassette_dir=cassette_dir, model=settings.groq_model, health=health)
    limiter = TokenBucketLimiter(
        redis_client, name="groq", limit_per_min=settings.llm_rate_limit_per_min, clock=clock
    )
    cache = SemanticCache(redis_client, ttl_s=settings.llm_cache_ttl_s)
    return GroqClient(
        api_key=settings.groq_api_key,
        model=settings.groq_model,
        timeout_s=settings.llm_timeout_s,
        breaker=breaker,
        limiter=limiter,
        cache=cache,
        health=health,
    )
