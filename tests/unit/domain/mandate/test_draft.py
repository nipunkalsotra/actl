"""§28 P8 instruction 2: U1's contract, in pure domain terms -- every
monetary bound must appear verbatim as a numeral in the user's own text,
and a missing required slot produces a clarification, never a default.
"""

from __future__ import annotations

from actl.domain.mandate.draft import (
    REQUIRED_SLOTS,
    ClarificationNeeded,
    MandateDraft,
    MandateDraftSlots,
    MoneyEvidence,
    build_draft,
    missing_required_slots,
    verify_money_evidence,
)


def _evidence(text: str, numeral: str) -> MoneyEvidence:
    start = text.index(numeral)
    return MoneyEvidence(numeral_text=numeral, start=start, end=start + len(numeral))


def test_book_me_something_nice_in_goa_asks_for_a_budget_and_invents_nothing() -> None:
    """The exact §28 P8 instruction 2 required test."""
    text = "book me something nice in Goa"
    slots = MandateDraftSlots(location="Goa")
    result = build_draft(text, slots)
    assert isinstance(result, ClarificationNeeded)
    assert "max_total_minor" in result.missing_slots
    assert any("budget" in q.lower() for q in result.questions)


def test_complete_slots_with_verbatim_evidence_produce_a_draft() -> None:
    text = "Book a hotel in Goa, check in 2026-09-12, 3 nights, 1 room, budget 5000 INR"
    slots = MandateDraftSlots(
        category="travel.hotel",
        location="Goa",
        check_in="2026-09-12",
        nights=3,
        rooms=1,
        currency="INR",
        max_total_minor_evidence=_evidence(text, "5000"),
    )
    result = build_draft(text, slots)
    assert isinstance(result, MandateDraft)
    assert result.max_total_minor == 500000
    assert result.max_unit_minor is None


def test_every_required_slot_missing_still_asks_only_the_top_priority_question() -> None:
    """Progressive collection: `missing_slots` still names every required
    field that's absent, but `questions` is only the single next thing to
    actually ask -- never the whole form dumped at once, even when
    genuinely everything is missing."""
    slots = MandateDraftSlots()
    missing = missing_required_slots(slots)
    assert missing == REQUIRED_SLOTS
    result = build_draft("book me something nice", slots)
    assert isinstance(result, ClarificationNeeded)
    assert len(result.questions) == 1
    assert result.questions == ("What's your total budget for this booking?",)


def test_evidence_span_not_matching_the_real_text_is_rejected() -> None:
    """The model claims a span, but the substring at that span isn't what
    it says -- code must recompute nothing from this, not even a partial
    trust; it must be treated exactly like the bound was never stated."""
    text = "book a hotel, budget is five thousand rupees"
    fabricated = MoneyEvidence(numeral_text="5000", start=0, end=4)  # "book", not "5000"
    assert verify_money_evidence(text, fabricated) is None


def test_evidence_numeral_not_a_plain_number_is_rejected() -> None:
    """"the model may not compute or infer an amount" -- a word-form
    numeral ("five thousand") is not itself a numeral, so no amount can be
    derived from it even if the span is genuinely correct."""
    text = "budget is five thousand rupees"
    evidence = _evidence(text, "five thousand")
    assert verify_money_evidence(text, evidence) is None


def test_verify_money_evidence_computes_minor_units_deterministically_in_code() -> None:
    """The model never supplies the minor-unit integer itself -- only the
    verbatim major-unit numeral text. x100 happens here, in code."""
    text = "budget is 1,250.50 for this trip"
    evidence = _evidence(text, "1,250.50")
    assert verify_money_evidence(text, evidence) == 125050


def test_a_claimed_but_unverifiable_money_bound_falls_back_to_missing_not_a_default() -> None:
    text = "book a hotel in Goa for 3 nights, 1 room, budget 5000 INR"
    slots = MandateDraftSlots(
        category="travel.hotel",
        location="Goa",
        check_in="2026-09-12",
        nights=3,
        rooms=1,
        currency="INR",
        # Evidence claims a span that does not contain "5000".
        max_total_minor_evidence=MoneyEvidence(numeral_text="5000", start=0, end=4),
    )
    result = build_draft(text, slots)
    assert isinstance(result, ClarificationNeeded)
    assert "max_total_minor" in result.missing_slots
