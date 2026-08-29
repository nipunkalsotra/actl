"""§17.2: "Schema-repair loop, bounded at two attempts." Pure-ish unit
tests against `ScriptedLLMClient` -- no network, no Redis, no breaker.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from actl.application.ports import LLMUnavailable
from actl.infrastructure.llm.repair import complete_json_with_repair
from tests.support.fake_llm_client import ScriptedLLMClient


class _Schema(BaseModel):
    sku: str
    qty: int


def _repair_prompt(original: str, error: ValidationError) -> str:
    return f"{original}\n\nFix this error: {error}"


@pytest.mark.asyncio
async def test_first_attempt_valid_returns_immediately() -> None:
    llm = ScriptedLLMClient(json_responses=[{"sku": "X", "qty": 2}])
    result = await complete_json_with_repair(
        llm,
        schema=_Schema,
        system="sys",
        user_prompt="extract",
        build_repair_prompt=_repair_prompt,
        max_tokens=100,
    )
    assert result == _Schema(sku="X", qty=2)
    assert len(llm.json_calls) == 1


@pytest.mark.asyncio
async def test_second_attempt_repairs_after_first_is_invalid() -> None:
    llm = ScriptedLLMClient(
        json_responses=[{"sku": "X", "qty": "not-a-number"}, {"sku": "X", "qty": 3}]
    )
    result = await complete_json_with_repair(
        llm,
        schema=_Schema,
        system="sys",
        user_prompt="extract",
        build_repair_prompt=_repair_prompt,
        max_tokens=100,
    )
    assert result == _Schema(sku="X", qty=3)
    assert len(llm.json_calls) == 2
    assert "Fix this error" in llm.json_calls[1][1]


@pytest.mark.asyncio
async def test_gives_up_after_two_attempts() -> None:
    """§28 P8 exit criteria: test_schema_repair_gives_up_after_two_attempts."""
    llm = ScriptedLLMClient(
        json_responses=[{"sku": "X", "qty": "bad"}, {"sku": "X", "qty": "still-bad"}]
    )
    with pytest.raises(LLMUnavailable):
        await complete_json_with_repair(
            llm,
            schema=_Schema,
            system="sys",
            user_prompt="extract",
            build_repair_prompt=_repair_prompt,
            max_tokens=100,
        )
    assert len(llm.json_calls) == 2  # never a third attempt


@pytest.mark.asyncio
async def test_underlying_llm_failure_propagates_without_a_repair_attempt() -> None:
    llm = ScriptedLLMClient(json_responses=[])  # exhausted immediately
    with pytest.raises(LLMUnavailable):
        await complete_json_with_repair(
            llm,
            schema=_Schema,
            system="sys",
            user_prompt="extract",
            build_repair_prompt=_repair_prompt,
            max_tokens=100,
        )
    assert len(llm.json_calls) == 1
