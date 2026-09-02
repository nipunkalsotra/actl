"""§17.1 U1: mandate extraction -> MandateDraft. Pure: no I/O, no LLM call
lives here. This module defines the draft shape and the invariant the
application layer must enforce before trusting anything the model claims:
every monetary bound's evidence must be a verbatim numeral substring of
the user's own text, at the span the model names -- and the minor-unit
amount is *computed here*, deterministically, from that verbatim text,
never accepted as a number the model itself computed (§17.1: "the model
may not compute or infer an amount").

A MandateDraft is not a Mandate (§9.1's DRAFT status): it has no
spec_hash, no signature, and nothing in this module or its callers can
authorize a money action from one -- application/gate.py never imports
this module.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field

# The slots §9.1's DRAFT -> PENDING_CONFIRM guard requires ("all required
# bounds present; no field inferred from silence") before a draft could
# ever be rendered back to the human for confirmation. `category` is
# included even though this catalog is travel.hotel-only today: assuming
# it from silence is exactly the "obvious, harmless-looking default" the
# guard forbids -- it has no carve-out for "only one possible value."
REQUIRED_SLOTS: tuple[str, ...] = (
    "category",
    "location",
    "check_in",
    "nights",
    "rooms",
    "currency",
    "max_total_minor",
)

_SLOT_QUESTIONS: dict[str, str] = {
    "category": "What kind of purchase is this (e.g. a hotel stay)?",
    "location": "Which city (and country) should I book in?",
    "check_in": "What check-in date do you want?",
    "nights": "How many nights?",
    "rooms": "How many rooms?",
    "currency": "What currency is your budget in?",
    "max_total_minor": "What's your total budget for this booking?",
}

# Which single missing slot to actually ask about next -- distinct from
# REQUIRED_SLOTS' declaration order. Budget goes first: it's the one bound
# this system can never silently default (§17.1), so a vague first message
# should narrow straight to the safety-critical question rather than the
# (structurally fixed, single-catalog) category slot. Progressive
# collection asks one question per turn instead of the whole form at once.
_QUESTION_PRIORITY: tuple[str, ...] = (
    "max_total_minor",
    "location",
    "check_in",
    "nights",
    "rooms",
    "category",
    "currency",
)


def _next_questions(missing: tuple[str, ...]) -> tuple[str, ...]:
    for slot in _QUESTION_PRIORITY:
        if slot in missing:
            return (_SLOT_QUESTIONS[slot],)
    return ()


class MoneyEvidence(BaseModel):
    """One extracted monetary bound, with the exact substring of the
    user's own conversation text it was read from. Neither `numeral_text`
    nor `start`/`end` is trusted until `verify_money_evidence` re-checks
    both against the real conversation text -- this is only the model's
    *claim*."""

    model_config = ConfigDict(frozen=True)

    numeral_text: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)


class MandateDraftSlots(BaseModel):
    """Everything U1 may fill from the conversation. Every field is
    optional -- None means "not yet said," never "assume a default."
    Money fields carry evidence instead of a bare minor-unit integer: the
    integer is derived later, in code, from the verbatim evidence text,
    never accepted directly from the model.

    `guests`/`refundable` are informational only -- neither is in
    REQUIRED_SLOTS (there is no mandate-schema "guests" bound, and
    refundability is set via the review card, not extracted); they exist
    so the chat can acknowledge what it understood without discarding it."""

    model_config = ConfigDict(frozen=True)

    category: str | None = None
    location: str | None = None
    check_in: str | None = None
    nights: int | None = None
    rooms: int | None = None
    currency: str | None = None
    max_total_minor_evidence: MoneyEvidence | None = None
    max_unit_minor_evidence: MoneyEvidence | None = None
    guests: int | None = None
    refundable: bool | None = None


class MandateDraft(BaseModel):
    """§9.1 DRAFT. Not a Mandate: no spec_hash, no signature, no row in
    the mandates table -- purely advisory data a human still has to
    confirm (through the existing, unmodified P1/P2 mandate-locking path)
    before anything here can authorize a purchase."""

    model_config = ConfigDict(frozen=True)

    slots: MandateDraftSlots
    max_total_minor: int
    max_unit_minor: int | None = None


class ClarificationNeeded(BaseModel):
    """U1's fallback contract (§17.1): "ask one direct question per
    missing bound." `missing_slots` names every §9.1-required field the
    draft does not have, in `REQUIRED_SLOTS` order. `questions` is the
    single highest-priority question to actually ask right now (see
    `_QUESTION_PRIORITY`) -- progressive collection, not the whole form at
    once. `slots` carries everything already understood (including the
    non-required `guests`/`refundable`) so a caller can acknowledge it
    instead of repeating a generic ask-about-everything message.
    `max_total_minor` is the already-*verified* budget (computed the same
    way as MandateDraft's, never the model's own number) when one other
    required slot is still missing -- so a caller can say "up to ₹10,000"
    without waiting for the draft to be otherwise complete."""

    model_config = ConfigDict(frozen=True)

    missing_slots: tuple[str, ...]
    questions: tuple[str, ...]
    slots: MandateDraftSlots
    max_total_minor: int | None = None


def missing_required_slots(slots: MandateDraftSlots) -> tuple[str, ...]:
    present = {
        "category": slots.category is not None,
        "location": slots.location is not None,
        "check_in": slots.check_in is not None,
        "nights": slots.nights is not None,
        "rooms": slots.rooms is not None,
        "currency": slots.currency is not None,
        "max_total_minor": slots.max_total_minor_evidence is not None,
    }
    return tuple(slot for slot in REQUIRED_SLOTS if not present[slot])


def verify_money_evidence(conversation_text: str, evidence: MoneyEvidence) -> int | None:
    """§17.1: "every monetary value must appear verbatim as a numeral in
    the user's own text; the model may not compute or infer an amount."
    Re-checks both of the model's claims against the real text -- the span
    actually contains that exact substring, and the substring is a plain
    decimal numeral -- then computes the minor-unit integer *here*, by
    simple x100 arithmetic, never from a value the model itself
    calculated. Returns None (not the model's number, not a fallback
    guess) if either check fails; the caller must treat that exactly like
    the bound being absent, never substitute a default."""
    if evidence.end > len(conversation_text):
        return None
    if conversation_text[evidence.start : evidence.end] != evidence.numeral_text:
        return None
    cleaned = evidence.numeral_text.strip().lstrip("₹$").replace(",", "").strip()
    # "10k" is a common, unambiguous shorthand for 10,000 -- the numeral
    # itself is still required to appear verbatim in the user's text
    # (checked above); this only interprets the trailing suffix, it never
    # lets the model supply a multiplier of its own.
    multiplier = 1
    if cleaned[-1:] in ("k", "K"):
        multiplier = 1000
        cleaned = cleaned[:-1]
    try:
        major = Decimal(cleaned)
    except InvalidOperation:
        return None
    if major <= 0:
        return None
    major *= multiplier
    minor = major * 100
    if minor != minor.to_integral_value():
        return None
    return int(minor)


def build_draft(
    conversation_text: str, slots: MandateDraftSlots
) -> MandateDraft | ClarificationNeeded:
    """The single orchestration point: missing-slot detection first, then
    evidence verification for whatever money fields are claimed present --
    a claimed-but-unverifiable bound is folded back into "missing," never
    silently dropped or defaulted."""
    missing = list(missing_required_slots(slots))

    max_total_minor: int | None = None
    if slots.max_total_minor_evidence is not None:
        max_total_minor = verify_money_evidence(conversation_text, slots.max_total_minor_evidence)
        if max_total_minor is None and "max_total_minor" not in missing:
            missing.append("max_total_minor")

    max_unit_minor: int | None = None
    if slots.max_unit_minor_evidence is not None:
        max_unit_minor = verify_money_evidence(conversation_text, slots.max_unit_minor_evidence)

    if missing:
        ordered = tuple(slot for slot in REQUIRED_SLOTS if slot in missing)
        return ClarificationNeeded(
            missing_slots=ordered,
            questions=_next_questions(ordered),
            slots=slots,
            max_total_minor=max_total_minor,
        )

    assert max_total_minor is not None  # not missing => verified above
    return MandateDraft(
        slots=slots, max_total_minor=max_total_minor, max_unit_minor=max_unit_minor
    )
