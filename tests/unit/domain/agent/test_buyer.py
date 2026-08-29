"""§28 P7 instruction 6: deterministic buyer-agent filtering and ranking.
Pure -- no I/O, no LLM, no randomness."""

from __future__ import annotations

from actl.domain.agent.buyer import filter_and_rank, filter_candidates, rank
from actl.domain.catalog.models import (
    CatalogAttributes,
    CatalogItem,
    CatalogLocation,
    CatalogPolicy,
)
from actl.domain.mandate.models import (
    Delegate,
    Mandate,
    MandateBounds,
    MandateControls,
    MandateIntent,
    MandateTemporal,
    Principal,
)


def _item(
    sku: str,
    *,
    price: int = 200000,
    rating: float = 4.0,
    category: str = "travel.hotel",
    merchant_id: str = "mrc_ok",
    city: str = "Goa",
    available_units: int = 5,
    refundable: bool = True,
) -> CatalogItem:
    return CatalogItem(
        sku=sku,
        category=category,
        merchant_id=merchant_id,
        unit="night",
        unit_price_minor=price,
        available_units=available_units,
        location=CatalogLocation(city=city, country="IN"),
        attributes=CatalogAttributes(rating=rating, sea_facing=True, breakfast_included=True),
        policy=CatalogPolicy(
            refundable=refundable,
            cancellation_window_h=48,
            instant_confirm=True,
            taxes_included=True,
        ),
        version=1,
        quote_required=True,
    )


def _mandate(
    *,
    max_unit_minor: int = 300000,
    allowed_categories: list[str] | None = None,
    blocked_merchants: list[str] | None = None,
    require_refundable: bool = True,
    location: str = "Goa, IN",
) -> Mandate:
    return Mandate(
        mandate_id="mdt_test",
        version=1,
        principal=Principal(type="human", id="usr_test"),
        delegate=Delegate(type="agent", id="agt_test", key_id="ed25519:test"),
        intent=MandateIntent(
            category="travel.hotel", location=location, check_in="2026-09-12", nights=3, rooms=1
        ),
        bounds=MandateBounds(
            currency="INR",
            max_total_minor=900000,
            max_unit_minor=max_unit_minor,
            max_transactions=1,
            allowed_categories=allowed_categories or ["travel.hotel"],
            blocked_merchants=blocked_merchants or [],
            require_refundable=require_refundable,
            max_price_delta_bps=0,
        ),
        temporal=MandateTemporal(
            not_before="2026-01-01T00:00:00.000Z",
            expires_at="2027-01-01T00:00:00.000Z",
            quote_ttl_s=120,
        ),
        controls=MandateControls(human_confirm_required=True, revocable=True),
    )


def test_filter_excludes_over_the_unit_cap() -> None:
    mandate = _mandate(max_unit_minor=300000)
    items = [_item("CHEAP", price=250000), _item("EXPENSIVE", price=350000)]

    result = filter_candidates(items, mandate)

    assert [i.sku for i in result] == ["CHEAP"]


def test_filter_excludes_disallowed_category() -> None:
    mandate = _mandate(allowed_categories=["travel.hotel"])
    items = [_item("HOTEL", category="travel.hotel"), _item("FLIGHT", category="travel.flight")]

    result = filter_candidates(items, mandate)

    assert [i.sku for i in result] == ["HOTEL"]


def test_filter_excludes_blocked_merchant() -> None:
    mandate = _mandate(blocked_merchants=["mrc_bad"])
    items = [_item("OK", merchant_id="mrc_ok"), _item("BLOCKED", merchant_id="mrc_bad")]

    result = filter_candidates(items, mandate)

    assert [i.sku for i in result] == ["OK"]


def test_filter_excludes_sold_out_items() -> None:
    mandate = _mandate()
    items = [_item("IN-STOCK", available_units=1), _item("SOLD-OUT", available_units=0)]

    result = filter_candidates(items, mandate)

    assert [i.sku for i in result] == ["IN-STOCK"]


def test_filter_excludes_non_refundable_when_required() -> None:
    mandate = _mandate(require_refundable=True)
    items = [_item("REFUNDABLE", refundable=True), _item("FINAL-SALE", refundable=False)]

    result = filter_candidates(items, mandate)

    assert [i.sku for i in result] == ["REFUNDABLE"]


def test_filter_excludes_a_different_city() -> None:
    mandate = _mandate(location="Goa, IN")
    items = [_item("GOA", city="Goa"), _item("MUMBAI", city="Mumbai")]

    result = filter_candidates(items, mandate)

    assert [i.sku for i in result] == ["GOA"]


def test_filter_excludes_every_infeasible_candidate_at_once() -> None:
    mandate = _mandate(max_unit_minor=300000, require_refundable=True)
    items = [
        _item("VALID", price=250000, refundable=True),
        _item("TOO-EXPENSIVE", price=999999),
        _item("SOLD-OUT", available_units=0),
        _item("NON-REFUNDABLE", refundable=False),
        _item("WRONG-CITY", city="Mumbai"),
        _item("WRONG-CATEGORY", category="travel.flight"),
    ]

    result = filter_candidates(items, mandate)

    assert [i.sku for i in result] == ["VALID"]


def test_rank_orders_price_ascending_then_rating_descending() -> None:
    items = [
        _item("MID-PRICE-LOW-RATING", price=250000, rating=3.5),
        _item("CHEAP", price=200000, rating=4.0),
        _item("MID-PRICE-HIGH-RATING", price=250000, rating=4.8),
    ]

    result = rank(items)

    assert [i.sku for i in result] == ["CHEAP", "MID-PRICE-HIGH-RATING", "MID-PRICE-LOW-RATING"]


def test_rank_tie_breaks_deterministically_by_sku() -> None:
    items = [
        _item("ZEBRA", price=200000, rating=4.5),
        _item("ALPHA", price=200000, rating=4.5),
        _item("MIKE", price=200000, rating=4.5),
    ]

    result = rank(items)

    assert [i.sku for i in result] == ["ALPHA", "MIKE", "ZEBRA"]


def test_rank_is_stable_across_repeated_runs() -> None:
    items = [
        _item("A", price=300000, rating=4.1),
        _item("B", price=200000, rating=4.9),
        _item("C", price=200000, rating=4.9),
        _item("D", price=250000, rating=3.0),
    ]

    first = [i.sku for i in rank(items)]
    for _ in range(20):
        assert [i.sku for i in rank(items)] == first


def test_filter_and_rank_combines_both_deterministically() -> None:
    mandate = _mandate(max_unit_minor=300000)
    items = [
        _item("TOO-EXPENSIVE", price=999999, rating=5.0),
        _item("BEST", price=200000, rating=4.9),
        _item("CHEAPEST-LOWER-RATED", price=200000, rating=3.0),
        _item("MID", price=250000, rating=4.0),
    ]

    result = filter_and_rank(items, mandate)

    assert [i.sku for i in result] == ["BEST", "CHEAPEST-LOWER-RATED", "MID"]
