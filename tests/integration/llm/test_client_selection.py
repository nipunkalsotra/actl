"""§28 P8 instruction 1: "LLM_ENABLED=false must completely disable Groq
calls" -- proven by construction: `NullLLMClient` never even builds a
Groq client or reads GROQ_API_KEY, and `build_llm_client` picks it
whenever `llm_enabled` is False, before DEMO_REPLAY or anything else is
even consulted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from redis.asyncio import Redis

from actl.application.ports import LLMUnavailable
from actl.config import Settings
from actl.infrastructure.llm.canonical_prompt import canonical_prompt_key
from actl.infrastructure.llm.factory import build_llm_client
from actl.infrastructure.llm.fallback import NullLLMClient
from actl.infrastructure.llm.groq_client import GroqClient
from actl.infrastructure.llm.replay_client import ReplayLLMClient
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import SystemClock

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_null_client_always_raises_and_touches_nothing() -> None:
    client = NullLLMClient()
    with pytest.raises(LLMUnavailable):
        await client.complete_json(system="s", user="u", max_tokens=10)
    with pytest.raises(LLMUnavailable):
        await client.complete_text(system="s", user="u", max_tokens=10)


async def test_llm_enabled_false_selects_null_client_even_with_demo_replay_also_set(
    redis_client: Redis,
) -> None:
    settings = Settings(llm_enabled=False, demo_replay=True, groq_api_key="")
    client = build_llm_client(
        settings,
        redis_client=redis_client,
        breaker=CircuitBreaker(name="groq-test", clock=SystemClock()),
        clock=SystemClock(),
    )
    assert isinstance(client, NullLLMClient)


async def test_demo_replay_true_selects_replay_client_when_llm_enabled(
    redis_client: Redis,
) -> None:
    settings = Settings(llm_enabled=True, demo_replay=True, groq_api_key="")
    client = build_llm_client(
        settings,
        redis_client=redis_client,
        breaker=CircuitBreaker(name="groq-test", clock=SystemClock()),
        clock=SystemClock(),
    )
    assert isinstance(client, ReplayLLMClient)


async def test_normal_config_selects_the_real_groq_client(redis_client: Redis) -> None:
    settings = Settings(llm_enabled=True, demo_replay=False, groq_api_key="gsk_placeholder")
    client = build_llm_client(
        settings,
        redis_client=redis_client,
        breaker=CircuitBreaker(name="groq-test", clock=SystemClock()),
        clock=SystemClock(),
    )
    assert isinstance(client, GroqClient)


async def test_replay_client_serves_a_committed_cassette(tmp_path: Path) -> None:
    key = canonical_prompt_key(mode="json", model="openai/gpt-oss-120b", system="s", user="u")
    (tmp_path / f"{key}.json").write_text(json.dumps({"response": {"ok": True}}))
    client = ReplayLLMClient(cassette_dir=tmp_path, model="openai/gpt-oss-120b")
    result = await client.complete_json(system="s", user="u", max_tokens=10)
    assert result == {"ok": True}


async def test_replay_client_raises_llm_unavailable_for_an_unrecorded_prompt(
    tmp_path: Path,
) -> None:
    client = ReplayLLMClient(cassette_dir=tmp_path, model="openai/gpt-oss-120b")
    with pytest.raises(LLMUnavailable):
        await client.complete_json(system="s", user="never recorded", max_tokens=10)
