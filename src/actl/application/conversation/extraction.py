"""§17.1 U1: mandate extraction -> MandateDraft. LLM output is advisory
only: even a schema-valid response is re-verified in pure code
(domain.mandate.draft.build_draft) before anything is trusted -- the
money-evidence check is what actually decides whether a bound exists, not
the model's own claim about it.
"""

from __future__ import annotations

from actl.application.conversation.deterministic_fallback import deterministic_slots_from_text
from actl.application.ports import LLMClient, LLMUnavailable
from actl.domain.mandate.draft import (
    ClarificationNeeded,
    MandateDraft,
    MandateDraftSlots,
    build_draft,
)
from actl.infrastructure.llm.prompts.extraction import (
    SYSTEM_PROMPT,
    build_repair_prompt,
    build_user_prompt,
)
from actl.infrastructure.llm.repair import complete_json_with_repair

MAX_TOKENS = 600


async def extract_mandate_draft(
    llm: LLMClient, conversation_text: str
) -> MandateDraft | ClarificationNeeded:
    """Never raises for an LLM failure. `LLMUnavailable` (rate limit,
    timeout, circuit open, LLM_ENABLED=false, or the schema-repair loop
    exhausted) falls back to §17.1's own words: "a slot-filling form: ask
    one direct question per missing bound. Slower, still correct" --
    `deterministic_slots_from_text` still actually reads the user's text
    (structured regex matches only, nothing invented) rather than
    substituting a blank draft that would ask about every slot regardless
    of what was said."""
    try:
        slots = await complete_json_with_repair(
            llm,
            schema=MandateDraftSlots,
            system=SYSTEM_PROMPT,
            user_prompt=build_user_prompt(conversation_text),
            build_repair_prompt=build_repair_prompt,
            max_tokens=MAX_TOKENS,
        )
    except LLMUnavailable:
        slots = deterministic_slots_from_text(conversation_text)

    return build_draft(conversation_text, slots)
