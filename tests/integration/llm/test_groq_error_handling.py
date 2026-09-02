"""Narrow follow-up: a real Groq HTTP 400 must fall back deterministically,
must never leak a secret/prompt/full provider body into a log or the
`LLMUnavailable` message it raises, and must never flip `LLMHealth` to
"succeeded" -- proven against the actual `groq.BadRequestError` shape (a
real `httpx.Response`/`httpx.Request` pair), not a hand-waved stand-in.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
from groq import BadRequestError
from redis.asyncio import Redis

from actl.application.conversation.extraction import extract_mandate_draft
from actl.application.ports import LLMUnavailable
from actl.domain.mandate.draft import ClarificationNeeded
from actl.infrastructure.cache.rate_limit import TokenBucketLimiter
from actl.infrastructure.cache.semantic_cache import SemanticCache
from actl.infrastructure.llm.groq_client import GroqClient
from actl.infrastructure.llm.health import LLMHealth
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import SystemClock

pytestmark = pytest.mark.asyncio(loop_scope="session")

_FAKE_SECRET = "gsk_should_never_appear_in_any_log_or_exception_text"
_SENSITIVE_BODY_FRAGMENT = "sensitive-request-fragment-should-never-be-logged"


def _real_bad_request_error() -> BadRequestError:
    """Builds the exact exception the groq SDK itself raises for a 400 --
    a real httpx.Request/Response pair, not a stub -- so the assertions
    below prove something about the real exception shape, including the
    fields (`.body`, `.message`) a naive `str(exc)` would have exposed."""
    request = httpx.Request(
        "POST",
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"authorization": f"Bearer {_FAKE_SECRET}"},
    )
    body = {
        "error": {
            "message": f"invalid request: {_SENSITIVE_BODY_FRAGMENT}",
            "type": "invalid_request_error",
        }
    }
    response = httpx.Response(400, request=request, json=body)
    return BadRequestError(f"Error code: 400 - {body}", response=response, body=body)


def _make_client(redis_client: Redis, *, health: LLMHealth | None = None) -> GroqClient:
    clock = SystemClock()
    limiter = TokenBucketLimiter(redis_client, name="groq-test", limit_per_min=20, clock=clock)
    cache = SemanticCache(redis_client, ttl_s=60)
    breaker = CircuitBreaker(name="groq-test", clock=clock)
    client = GroqClient(
        api_key=_FAKE_SECRET,
        model="openai/gpt-oss-120b",
        timeout_s=5,
        breaker=breaker,
        limiter=limiter,
        cache=cache,
        health=health,
    )
    client._client.chat.completions.create = AsyncMock(side_effect=_real_bad_request_error())  # type: ignore[method-assign]
    return client


async def test_a_real_400_falls_back_to_llm_unavailable(redis_client: Redis) -> None:
    client = _make_client(redis_client)
    with pytest.raises(LLMUnavailable):
        await client.complete_json(system="s", user="u", max_tokens=10)


async def test_extraction_still_falls_back_deterministically_on_a_400(
    redis_client: Redis,
) -> None:
    """The end-to-end proof: a provider 400 must reach the same
    deterministic fallback LLM_ENABLED=false does, not a broken/generic
    response."""
    client = _make_client(redis_client)
    result = await extract_mandate_draft(client, "2 night hotel stay in Goa budget 10k")
    assert isinstance(result, ClarificationNeeded)
    assert result.slots.category == "travel.hotel"
    assert result.slots.location == "Goa"
    assert result.slots.nights == 2


async def test_400_is_logged_with_only_status_type_and_model_never_a_secret(
    redis_client: Redis, capsys: pytest.CaptureFixture[str]
) -> None:
    client = _make_client(redis_client)
    with pytest.raises(LLMUnavailable):
        await client.complete_json(system="top secret system prompt", user="u", max_tokens=10)

    captured = capsys.readouterr()
    log_output = captured.out + captured.err

    assert _FAKE_SECRET not in log_output
    assert "Bearer" not in log_output
    assert "authorization" not in log_output.lower()
    assert _SENSITIVE_BODY_FRAGMENT not in log_output
    assert "top secret system prompt" not in log_output

    assert "400" in log_output
    assert "invalid_request_error" in log_output
    assert "openai/gpt-oss-120b" in log_output


async def test_llm_unavailable_message_itself_never_carries_the_raw_body(
    redis_client: Redis,
) -> None:
    """Defence in depth: even if some future caller logs the exception
    directly (instead of relying on groq_client's own sanitized log
    line), its own message must not carry the raw provider body/message
    text -- only the sanitized status/type summary."""
    client = _make_client(redis_client)
    with pytest.raises(LLMUnavailable) as exc_info:
        await client.complete_json(system="s", user="u", max_tokens=10)

    message = str(exc_info.value)
    assert _SENSITIVE_BODY_FRAGMENT not in message
    assert _FAKE_SECRET not in message
    assert "400" in message
    assert "invalid_request_error" in message


async def test_health_never_flips_to_succeeded_after_a_400(redis_client: Redis) -> None:
    health = LLMHealth()
    client = _make_client(redis_client, health=health)
    with pytest.raises(LLMUnavailable):
        await client.complete_json(system="s", user="u", max_tokens=10)
    assert health.succeeded_once is False


async def test_health_flips_to_succeeded_only_after_a_real_success(redis_client: Redis) -> None:
    health = LLMHealth()
    assert health.succeeded_once is False
    health.mark_success()
    assert health.succeeded_once is True
