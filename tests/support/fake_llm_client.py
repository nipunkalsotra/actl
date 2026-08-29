"""Test-only `LLMClient` doubles -- not shipped as production
infrastructure (unlike `SimulatorAdapter`, which is a real
`PAYMENT_PROVIDER` config choice, "force every LLM call to raise" is
purely a test concern, §28 P8 instruction 10).
"""

from __future__ import annotations

from collections.abc import Sequence

from actl.application.ports import LLMUnavailable


class AlwaysFailsLLMClient:
    """§28 P8 instruction 10 / the critical resilience test: every call
    raises `LLMUnavailable`, unconditionally -- the same contract a real
    circuit-open or LLM_ENABLED=false client gives every caller."""

    async def complete_json(
        self, *, system: str, user: str, max_tokens: int
    ) -> dict[str, object]:
        raise LLMUnavailable("forced failure (test double)")

    async def complete_text(self, *, system: str, user: str, max_tokens: int) -> str:
        raise LLMUnavailable("forced failure (test double)")


class ScriptedLLMClient:
    """Returns each entry of `json_responses`/`text_responses` in order,
    one per call, so a repair-loop test can script "bad JSON, then a
    schema-valid repair" or similar exact sequences deterministically.
    Raises `LLMUnavailable` once the script runs out."""

    def __init__(
        self,
        *,
        json_responses: Sequence[dict[str, object]] = (),
        text_responses: Sequence[str] = (),
    ) -> None:
        self._json_responses = list(json_responses)
        self._text_responses = list(text_responses)
        self.json_calls: list[tuple[str, str]] = []
        self.text_calls: list[tuple[str, str]] = []

    async def complete_json(
        self, *, system: str, user: str, max_tokens: int
    ) -> dict[str, object]:
        self.json_calls.append((system, user))
        if not self._json_responses:
            raise LLMUnavailable("scripted responses exhausted")
        return self._json_responses.pop(0)

    async def complete_text(self, *, system: str, user: str, max_tokens: int) -> str:
        self.text_calls.append((system, user))
        if not self._text_responses:
            raise LLMUnavailable("scripted responses exhausted")
        return self._text_responses.pop(0)
