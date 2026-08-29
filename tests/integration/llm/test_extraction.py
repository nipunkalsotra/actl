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
async def test_llm_unavailable_falls_back_to_asking_about_every_required_slot() -> None:
    """§17.1: "Fall back to a slot-filling form: ask one direct question
    per missing bound. Slower, still correct." """
    llm = AlwaysFailsLLMClient()
    result = await extract_mandate_draft(llm, "book me something nice in Goa")
    assert isinstance(result, ClarificationNeeded)
    assert len(result.missing_slots) == 7  # every REQUIRED_SLOTS entry


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
