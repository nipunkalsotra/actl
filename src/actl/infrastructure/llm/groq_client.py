"""§17: GroqClient -- the concrete `LLMClient` adapter, and the only
module in this codebase allowed to import the `groq` SDK (enforced by
.importlinter contract 5 and
tests/architecture/test_boundaries.py::test_llm_module_has_no_credentials).

Every call goes through, in order: semantic cache read -> rate limiter ->
circuit breaker -> the real Groq API call -> cache write. Any failure at
any stage raises `LLMUnavailable` -- never a raw groq/Redis exception --
so application code has exactly one failure mode to react to (§17.2), and
the money path never depends on Groq's own exception hierarchy.

Never logs (here, or in the sanitized `LLMUnavailable` message a caller
might see): the Authorization header, GROQ_API_KEY, a raw prompt, or
Groq's full error body (`error.message` can echo request fragments back,
per Groq's own docs at console.groq.com/docs/errors) -- only the HTTP
status, Groq's own short `error.type` classification (e.g.
"invalid_request_error"), and the configured (non-secret) model id, same
spirit as `infrastructure/providers/razorpay/adapter.py`'s
`_safe_error_body`.
"""

from __future__ import annotations

import json
from typing import Any, cast

from groq import APIError, AsyncGroq

from actl.application.ports import LLMUnavailable
from actl.infrastructure.cache.rate_limit import RateLimitUnavailable, TokenBucketLimiter
from actl.infrastructure.cache.semantic_cache import SemanticCache
from actl.infrastructure.llm.canonical_prompt import canonical_prompt_key
from actl.infrastructure.llm.health import LLMHealth
from actl.platform import metrics
from actl.platform.breaker import CircuitBreaker
from actl.platform.errors import CircuitOpenError
from actl.platform.logging import get_logger

logger = get_logger(__name__)


def _sanitized_error_type(exc: APIError) -> str | None:
    """Groq's documented error body is `{"error": {"message", "type"}}` --
    `type` is a short classification (e.g. "invalid_request_error"), safe
    to log. `message` is free text that can include request fragments, so
    it is deliberately never read here."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            error_type = error.get("type")
            if isinstance(error_type, str):
                return error_type
    return None


class GroqClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_s: float,
        breaker: CircuitBreaker,
        limiter: TokenBucketLimiter,
        cache: SemanticCache,
        health: LLMHealth | None = None,
    ) -> None:
        self._client = AsyncGroq(api_key=api_key, timeout=timeout_s)
        self._model = model
        self._breaker = breaker
        self._limiter = limiter
        self._cache = cache
        self._health = health or LLMHealth()

    async def complete_json(self, *, system: str, user: str, max_tokens: int) -> dict[str, object]:
        result = await self._complete(mode="json", system=system, user=user, max_tokens=max_tokens)
        if not isinstance(result, dict):
            raise LLMUnavailable("Groq response was not a JSON object")
        return cast(dict[str, object], result)

    async def complete_text(self, *, system: str, user: str, max_tokens: int) -> str:
        result = await self._complete(mode="text", system=system, user=user, max_tokens=max_tokens)
        if not isinstance(result, str):
            raise LLMUnavailable("Groq response was not text")
        return result

    async def _complete(self, *, mode: str, system: str, user: str, max_tokens: int) -> object:
        cache_key = canonical_prompt_key(mode=mode, model=self._model, system=system, user=user)
        cached = await self._cache.get(cache_key)
        if cached is not None:
            metrics.llm_cache_hits_total.inc()
            return cached

        try:
            allowed = await self._limiter.try_acquire()
        except RateLimitUnavailable as exc:
            raise LLMUnavailable(f"rate limiter unavailable: {exc}") from exc
        if not allowed:
            raise LLMUnavailable("LLM rate limit exceeded")

        async def _call() -> object:
            kwargs: dict[str, Any] = {
                "model": self._model,
                "temperature": 0,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }
            if mode == "json":
                kwargs["response_format"] = {"type": "json_object"}
            response = await self._client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content or ""
            if mode == "json":
                parsed: object = json.loads(content)
                return parsed
            return content

        try:
            result = await self._breaker.call(_call)
        except CircuitOpenError as exc:
            raise LLMUnavailable(str(exc)) from exc
        except APIError as exc:
            status = getattr(exc, "status_code", None)
            error_type = _sanitized_error_type(exc)
            logger.warning(
                "groq.request_failed", status=status, error_type=error_type, model=self._model
            )
            raise LLMUnavailable(
                f"Groq API error (status={status}, type={error_type})"
            ) from exc
        except (json.JSONDecodeError, ValueError) as exc:
            raise LLMUnavailable(str(exc)) from exc

        self._health.mark_success()
        await self._cache.set(cache_key, result)
        return result
