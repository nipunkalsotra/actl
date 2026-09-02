"""§28 P8 instruction 2 / exit criteria: U1 mandate extraction. No real
Postgres/Redis needed here -- `extract_mandate_draft` only depends on the
`LLMClient` port, exercised against the `ScriptedLLMClient`/
`AlwaysFailsLLMClient` test doubles, matching the file's own directory
(tests/integration/llm) rather than tests/unit (§23: unit tests are
domain-only, no I/O port at all -- this one takes a port).
"""

from __future__ import annotations

import pytest

from actl.application.conversation.extraction import extract_mandate_draft
from actl.domain.mandate.draft import ClarificationNeeded, MandateDraft
from tests.support.fake_llm_client import AlwaysFailsLLMClient, ScriptedLLMClient


@pytest.mark.asyncio
async def test_extraction_refuses_to_invent_a_budget() -> None:
    """§28 P8 instruction 2's exact required test: "book me something nice
    in Goa" must ask for a budget and must not invent one."""
    text = "book me something nice in Goa"
    llm = ScriptedLLMClient(json_responses=[{"location": "Goa"}])
    result = await extract_mandate_draft(llm, text)
    assert isinstance(result, ClarificationNeeded)
    assert "max_total_minor" in result.missing_slots
    assert any("budget" in q.lower() for q in result.questions)


@pytest.mark.asyncio
async def test_complete_extraction_produces_a_draft_with_verified_budget() -> None:
    text = "Book a hotel in Goa, check in 2026-09-12, 3 nights, 1 room, budget 5000 INR"
    start = text.index("5000")
    llm = ScriptedLLMClient(
        json_responses=[
            {
                "category": "travel.hotel",
                "location": "Goa",
                "check_in": "2026-09-12",
                "nights": 3,
                "rooms": 1,
                "currency": "INR",
                "max_total_minor_evidence": {
                    "numeral_text": "5000",
                    "start": start,
                    "end": start + 4,
                },
            }
        ]
    )
    result = await extract_mandate_draft(llm, text)
    assert isinstance(result, MandateDraft)
    assert result.max_total_minor == 500000


@pytest.mark.asyncio
async def test_llm_unavailable_still_reads_the_text_instead_of_asking_about_everything() -> None:
    """§17.1: "Fall back to a slot-filling form: ask one direct question
    per missing bound. Slower, still correct." -- the deterministic
    fallback must actually parse what it can (here: "Goa" as location)
    rather than substituting a blank draft that asks about every slot
    regardless of input. Budget is still missing and still asked about
    first (never invented)."""
    llm = AlwaysFailsLLMClient()
    result = await extract_mandate_draft(llm, "book me something nice in Goa")
    assert isinstance(result, ClarificationNeeded)
    assert result.slots.location == "Goa"
    assert len(result.missing_slots) < 7  # not the generic all-fields case
    assert result.questions == ("What's your total budget for this booking?",)


@pytest.mark.asyncio
async def test_llm_unavailable_with_truly_empty_text_asks_about_budget_first() -> None:
    """Nothing recognisable was said: every slot the fallback can actually
    infer from text stays missing. `currency` is the one slot the
    deterministic fallback always fills (this deployment hardcodes "INR"
    downstream regardless -- see deterministic_fallback module docstring),
    so 6, not 7, are missing -- and the single question asked is still the
    highest-priority (budget) one, never an invented amount."""
    llm = AlwaysFailsLLMClient()
    result = await extract_mandate_draft(llm, "hello")
    assert isinstance(result, ClarificationNeeded)
    assert len(result.missing_slots) == 6
    assert result.questions == ("What's your total budget for this booking?",)


@pytest.mark.asyncio
async def test_a_hallucinated_amount_not_present_in_the_text_is_never_trusted() -> None:
    """The model claims a span/numeral that doesn't actually match the
    real conversation text -- verified in code (domain.mandate.draft), so
    a fabricated evidence claim cannot smuggle an invented budget through."""
    text = "book me something nice in Goa"
    llm = ScriptedLLMClient(
        json_responses=[
            {
                "location": "Goa",
                # Claims "50000" appears at [0:5] -- it doesn't ("book " is there).
                "max_total_minor_evidence": {"numeral_text": "50000", "start": 0, "end": 5},
            }
        ]
    )
    result = await extract_mandate_draft(llm, text)
    assert isinstance(result, ClarificationNeeded)
    assert "max_total_minor" in result.missing_slots


@pytest.mark.asyncio
async def test_malformed_json_that_never_validates_falls_back_via_the_repair_loop() -> None:
    llm = ScriptedLLMClient(
        json_responses=[
            {"nights": "not-a-number"},  # invalid type, attempt 1
            {"nights": "still-not-a-number"},  # invalid type, attempt 2 -- repair exhausted
        ]
    )
    result = await extract_mandate_draft(llm, "book me something nice in Goa")
    assert isinstance(result, ClarificationNeeded)
    assert len(llm.json_calls) == 2


# ---------------------------------------------------------------------------
# Deterministic fallback (LLM_ENABLED=false): progressive mandate collection.
# These reproduce the exact bug report -- the chat asking the same generic
# all-field message no matter what the buyer typed -- and its fix.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deterministic_fallback_narrows_to_a_single_next_field_with_partial_info() -> None:
    """"2 night hotel stay in Goa budget 10k" must acknowledge (category,
    location, nights, budget all parsed) and ask about only what's left --
    never the generic seven-question dump, and "10k" must resolve to
    exactly ten thousand rupees, never a guessed figure."""
    llm = AlwaysFailsLLMClient()
    result = await extract_mandate_draft(llm, "2 night hotel stay in Goa budget 10k")
    assert isinstance(result, ClarificationNeeded)
    assert result.slots.category == "travel.hotel"
    assert result.slots.location == "Goa"
    assert result.slots.nights == 2
    assert result.missing_slots == ("check_in", "rooms")
    assert result.questions == ("What check-in date do you want?",)


@pytest.mark.asyncio
async def test_deterministic_fallback_never_invents_a_budget_from_10k_shorthand() -> None:
    llm = AlwaysFailsLLMClient()
    result = await extract_mandate_draft(llm, "2 night hotel stay in Goa budget 10k")
    assert isinstance(result, ClarificationNeeded)
    text = "2 night hotel stay in Goa budget 10k"
    evidence = result.slots.max_total_minor_evidence
    assert evidence is not None
    assert text[evidence.start : evidence.end] == evidence.numeral_text == "10k"
    from actl.domain.mandate.draft import verify_money_evidence

    assert verify_money_evidence(text, evidence) == 1_000_000  # Rs 10,000 in minor units


@pytest.mark.asyncio
async def test_deterministic_fallback_multi_turn_merge_narrows_to_rooms_only() -> None:
    """Multi-turn partial-draft merge: the frontend resends the full
    cumulative transcript each turn (see ChatPanel.runExtraction), so a
    third turn's "15 September, refundable, 2 guests" is parsed alongside
    everything said in turns one and two -- only rooms is still missing."""
    llm = AlwaysFailsLLMClient()
    transcript = "\n".join(
        [
            "book me something nice in Goa",
            "2 night hotel stay in Goa budget 10k",
            "15 September, refundable, 2 guests",
        ]
    )
    result = await extract_mandate_draft(llm, transcript)
    assert isinstance(result, ClarificationNeeded)
    assert result.missing_slots == ("rooms",)
    assert result.questions == ("How many rooms?",)
    assert result.slots.check_in == "15 September"
    assert result.slots.refundable is True
    assert result.slots.guests == 2


@pytest.mark.asyncio
async def test_deterministic_fallback_completes_once_rooms_is_also_given() -> None:
    llm = AlwaysFailsLLMClient()
    transcript = "\n".join(
        [
            "2 night hotel stay in Goa budget 10k",
            "15 September, refundable, 2 guests, 1 room",
        ]
    )
    result = await extract_mandate_draft(llm, transcript)
    assert isinstance(result, MandateDraft)
    assert result.max_total_minor == 1_000_000
    assert result.slots.rooms == 1


@pytest.mark.asyncio
async def test_deterministic_fallback_a_later_correction_overrides_an_earlier_value() -> None:
    llm = AlwaysFailsLLMClient()
    transcript = "\n".join(["I need 2 nights", "actually make it 3 nights"])
    result = await extract_mandate_draft(llm, transcript)
    assert isinstance(result, ClarificationNeeded)
    assert result.slots.nights == 3
