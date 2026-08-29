"""§17.2: "Schema-repair loop, bounded at two attempts. Validation
failure returns the error to the model once; a second failure falls
through to the deterministic path. No unbounded retry loops." Generic
over any Pydantic model -- U1 (MandateDraft/ClarificationNeeded) and U2
(ranking response) both use this same two-attempt loop; the target
schema and prompts are supplied by the caller, this module holds only the
retry mechanics.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, ValidationError

from actl.application.ports import LLMClient, LLMUnavailable


async def complete_json_with_repair[ModelT: BaseModel](
    llm: LLMClient,
    *,
    schema: type[ModelT],
    system: str,
    user_prompt: str,
    build_repair_prompt: Callable[[str, ValidationError], str],
    max_tokens: int,
) -> ModelT:
    """Exactly two `LLMClient.complete_json` calls at most. Raises
    `LLMUnavailable` if either call fails outright (propagated as-is from
    `llm.complete_json`), or if both attempts produce JSON that still
    doesn't validate against `schema` -- either way, the caller's existing
    deterministic fallback is what runs next."""
    raw = await llm.complete_json(system=system, user=user_prompt, max_tokens=max_tokens)
    try:
        return schema.model_validate(raw)
    except ValidationError as first_error:
        repair_user = build_repair_prompt(user_prompt, first_error)
        raw2 = await llm.complete_json(system=system, user=repair_user, max_tokens=max_tokens)
        try:
            return schema.model_validate(raw2)
        except ValidationError as second_error:
            raise LLMUnavailable(
                f"schema repair exhausted after 2 attempts: {second_error}"
            ) from second_error
