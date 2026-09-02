"""§17.1 U1 deterministic fallback -- what actually runs when the LLM path
is unavailable (LLM_ENABLED=false, rate limit, circuit open, or the
schema-repair loop exhausted). Same trust rule as the LLM path: a field is
either an unambiguous match against the user's own text, or it stays None
-- nothing here is a guess, a default, or an inference from silence.

`category`/`location` are only filled when the text actually says a
recognisable keyword/city, matching `domain.mandate.draft`'s own "no
carve-out for only one possible value" stance even though this catalog
happens to be Goa-only travel.hotel today. `currency` is the one
deliberate exception: `create_mandate` (interfaces/http/routers/buyer.py)
hardcodes "INR" unconditionally regardless of anything extracted here --
this deployment has no other currency to offer -- so defaulting it costs
no real safety margin and spares a pointless question about a value
nothing downstream ever reads.
"""

from __future__ import annotations

import re

from actl.domain.mandate.draft import MandateDraftSlots, MoneyEvidence

_CATEGORY_RE = re.compile(r"\b(hotel|stay|resort|rooms?)\b", re.IGNORECASE)
_LOCATION_RE = re.compile(r"\bgoa\b", re.IGNORECASE)
_NIGHTS_RE = re.compile(r"\b(\d+)\s*[- ]?\s*nights?\b", re.IGNORECASE)
_ROOMS_RE = re.compile(r"\b(\d+)\s*rooms?\b", re.IGNORECASE)
_GUESTS_RE = re.compile(r"\b(\d+)\s*(?:guests?|people|adults?|persons?)\b", re.IGNORECASE)
_REFUND_RE = re.compile(r"\b(non[- ]?refundable|refundable)\b", re.IGNORECASE)

_MONTHS = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_FUZZY_DATE_RE = re.compile(
    rf"\b(?:\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTHS})|(?:{_MONTHS})\s+\d{{1,2}}(?:st|nd|rd|th)?)\b",
    re.IGNORECASE,
)

# A numeral only counts as a budget when it's clearly marked as money -- a
# bare "2" from "2 nights" must never be mistaken for a price. One of a
# currency mark, "budget"/"under"/"within"/"up to" must precede the number.
_MONEY_RE = re.compile(
    r"(?:₹|\brs\.?|\binr\b|\bbudget(?:\s+of)?|\bunder|\bwithin|\bup ?to)\s*"
    r"(\d[\d,]*(?:\.\d+)?\s?[kK]?)\b",
    re.IGNORECASE,
)


def _last_match(pattern: re.Pattern[str], text: str) -> re.Match[str] | None:
    """Last match wins: if a later message restates a field (a correction,
    e.g. "actually make it 3 nights"), that's the one that should stick --
    matching how the LLM path re-reads the whole cumulative transcript
    fresh on every turn too."""
    matches = list(pattern.finditer(text))
    return matches[-1] if matches else None


def deterministic_slots_from_text(conversation_text: str) -> MandateDraftSlots:
    category = "travel.hotel" if _CATEGORY_RE.search(conversation_text) else None
    location = "Goa" if _LOCATION_RE.search(conversation_text) else None

    nights_match = _last_match(_NIGHTS_RE, conversation_text)
    nights = int(nights_match.group(1)) if nights_match else None

    rooms_match = _last_match(_ROOMS_RE, conversation_text)
    rooms = int(rooms_match.group(1)) if rooms_match else None

    guests_match = _last_match(_GUESTS_RE, conversation_text)
    guests = int(guests_match.group(1)) if guests_match else None

    refund_match = _last_match(_REFUND_RE, conversation_text)
    refundable = (
        None if refund_match is None else not refund_match.group(1).lower().startswith("non")
    )

    date_match = _last_match(_ISO_DATE_RE, conversation_text) or _last_match(
        _FUZZY_DATE_RE, conversation_text
    )
    check_in = date_match.group(0) if date_match else None

    money_match = _last_match(_MONEY_RE, conversation_text)
    max_total_minor_evidence = (
        MoneyEvidence(
            numeral_text=money_match.group(1),
            start=money_match.start(1),
            end=money_match.end(1),
        )
        if money_match
        else None
    )

    return MandateDraftSlots(
        category=category,
        location=location,
        check_in=check_in,
        nights=nights,
        rooms=rooms,
        currency="INR",
        max_total_minor_evidence=max_total_minor_evidence,
        guests=guests,
        refundable=refundable,
    )
