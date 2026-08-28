"""Hypothesis strategies for Mandate / PurchaseIntent / PolicyContext (§10.3).

`mandate` and `intent` are independent `@given` parameters (matching the
architecture doc's own test signatures), but a handful of fields — category,
nights, rooms, currency, the mandate/intent hash pairing — have to agree for
*anything* to reach ALLOW. Drawing those independently over a wide range
means they almost never coincide, and the ALLOW-only properties below would
then only ever be checked vacuously (0 ALLOWs seen in practice). `st.shared`
gives both sides the same drawn value for a given key without making the
two strategies aware of each other, so those administrative fields always
line up and DENY/ALLOW turn on the fields these tests actually care about:
money caps, the temporal window, refund policy and price drift.

`intent_hash` is never drawn independently — it's derived from the rest of
the intent via `.map()` so it's always self-consistent, same as a real caller.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hypothesis import strategies as st

from actl.domain.mandate.models import (
    Delegate,
    Mandate,
    MandateBounds,
    MandateControls,
    MandateIntent,
    MandateTemporal,
    Principal,
)
from actl.domain.policy.rules import PolicyContext, PurchaseIntent, compute_intent_hash

_CATEGORIES = ["travel.hotel", "electronics.laptop"]
_MERCHANTS = ["good_merchant", "bad_merchant", "another_merchant"]
_BASE_TIME = datetime(2026, 8, 28, 9, 0, 0, tzinfo=UTC)
_CATALOG_VERSION = 1  # matches ctx_zero(); catalog freshness isn't what these tests probe

_shared_category = st.shared(st.sampled_from(_CATEGORIES), key="category")
_shared_nights = st.shared(st.integers(min_value=1, max_value=5), key="nights")
_shared_rooms = st.shared(st.integers(min_value=1, max_value=3), key="rooms")
_shared_currency = st.shared(st.sampled_from(["INR", "USD"]), key="currency")
_shared_spec_hash = st.shared(
    st.text(alphabet="0123456789abcdef", min_size=8, max_size=8).map(lambda h: f"sha256:{h}"),
    key="spec_hash",
)


def _money(max_value: int = 3_000_000) -> st.SearchStrategy[int]:
    return st.integers(min_value=0, max_value=max_value)


@st.composite
def mandates(draw: st.DrawFn) -> Mandate:
    # Bias the window to usually (not always) bracket _BASE_TIME, so
    # temporal.window passes often enough for the other rules to matter:
    # `before` is how many seconds *before* _BASE_TIME the window opens
    # (mostly positive/safe, occasionally negative to hit NOT_YET_VALID).
    before = draw(st.integers(min_value=-600, max_value=3600))
    after = draw(st.integers(min_value=-600, max_value=3600))
    not_before = _BASE_TIME - timedelta(seconds=before)
    expires_at = _BASE_TIME + timedelta(seconds=max(after, 1))

    category = draw(_shared_category)
    draft = Mandate(
        mandate_id="mdt_" + draw(st.text(alphabet="0123456789ABCDEF", min_size=6, max_size=6)),
        version=draw(st.integers(min_value=1, max_value=5)),
        principal=Principal(type="human", id="usr_test"),
        delegate=Delegate(type="agent", id="agt_test", key_id="ed25519:test"),
        intent=MandateIntent(
            category=category,
            location="Goa, IN",
            check_in="2026-09-12",
            nights=draw(_shared_nights),
            rooms=draw(_shared_rooms),
        ),
        bounds=MandateBounds(
            currency=draw(_shared_currency),
            max_total_minor=draw(_money()),
            max_unit_minor=draw(_money()),
            max_transactions=draw(st.integers(min_value=1, max_value=5)),
            allowed_categories=draw(st.sampled_from([[category], _CATEGORIES])),
            blocked_merchants=draw(st.sampled_from([[], ["bad_merchant"]])),
            require_refundable=draw(st.booleans()),
            max_price_delta_bps=draw(st.integers(min_value=0, max_value=500)),
        ),
        temporal=MandateTemporal(not_before=not_before, expires_at=expires_at, quote_ttl_s=120),
        controls=MandateControls(human_confirm_required=True, revocable=True),
    )
    return draft.model_copy(update={"spec_hash": draw(_shared_spec_hash)})


@st.composite
def intents(draw: st.DrawFn) -> PurchaseIntent:
    quoted = draw(_money())
    # current_total_minor stays within a small *relative* delta of quoted
    # (§7: a quote pins a price to a TTL). A fixed absolute delta would
    # blow up the bps ratio whenever Hypothesis draws a small `quoted` —
    # which, since st.integers() is biased toward small magnitudes rather
    # than uniform, is often.
    delta_bps_target = draw(st.integers(min_value=-200, max_value=200))
    current = max(0, quoted + (quoted * delta_bps_target) // 10_000)
    intent = PurchaseIntent(
        currency=draw(_shared_currency),
        category=draw(_shared_category),
        merchant=draw(st.sampled_from(_MERCHANTS)),
        unit_price_minor=draw(_money()),
        total_minor=draw(_money()),
        nights=draw(_shared_nights),
        rooms=draw(_shared_rooms),
        refundable=draw(st.booleans()),
        quoted_total_minor=quoted,
        current_total_minor=current,
        catalog_version=_CATALOG_VERSION,
        mandate_spec_hash=draw(_shared_spec_hash),
        intent_hash="",
    )
    return intent.model_copy(update={"intent_hash": compute_intent_hash(intent)})


@st.composite
def contexts(draw: st.DrawFn) -> PolicyContext:
    offset_s = draw(st.integers(min_value=-3600, max_value=3600))
    now = _BASE_TIME + timedelta(seconds=offset_s)
    return PolicyContext(
        now=now,
        reserved_minor=draw(_money()),
        txn_count=draw(st.integers(min_value=0, max_value=5)),
        catalog_version=draw(st.integers(min_value=1, max_value=5)),
        decision_id="dec_" + draw(st.text(alphabet="0123456789ABCDEF", min_size=6, max_size=6)),
        decision_ttl_s=draw(st.integers(min_value=1, max_value=120)),
    )


def ctx_zero() -> PolicyContext:
    """A fixed baseline context: no budget reserved, no transactions used
    yet, catalog fresh, timestamp inside the strategies' base window."""
    return PolicyContext(
        now=_BASE_TIME,
        reserved_minor=0,
        txn_count=0,
        catalog_version=_CATALOG_VERSION,
        decision_id="dec_zero",
        decision_ttl_s=30,
    )
