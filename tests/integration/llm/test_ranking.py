"""§28 P8 instruction 3 / exit criteria: U2 candidate ranking over an
already-filtered list. Reuses P7's own `make_locked_mandate` test
fixture -- no real Postgres/Redis needed, `rank_candidates` only depends
on the `LLMClient` port and pure domain values.
"""

from __future__ import annotations

import pytest

from actl.application.conversation.ranking import rank_candidates
from actl.domain.catalog.models import (
    CatalogAttributes,
    CatalogItem,
    CatalogLocation,
    CatalogPolicy,
)
from tests.integration.db.conftest import make_locked_mandate
from tests.support.fake_llm_client import AlwaysFailsLLMClient, ScriptedLLMClient


def _item(
    sku: str,
    *,
    unit_price_minor: int,
    rating: float = 4.0,
    city: str = "Goa",
    refundable: bool = True,
) -> CatalogItem:
    return CatalogItem(
        sku=sku,
        category="travel.hotel",
        merchant_id="mrc_test",
        unit="night",
        unit_price_minor=unit_price_minor,
        available_units=5,
        location=CatalogLocation(city=city, country="IN"),
        attributes=CatalogAttributes(rating=rating, sea_facing=True, breakfast_included=True),
        policy=CatalogPolicy(
            refundable=refundable, cancellation_window_h=48, instant_confirm=True,
            taxes_included=True,
        ),
        version=1,
        quote_required=True,
    )


def _candidates() -> list[CatalogItem]:
    return [
        _item("HTL-A", unit_price_minor=200000, rating=4.0),
        _item("HTL-B", unit_price_minor=150000, rating=3.5),
        _item("HTL-C", unit_price_minor=250000, rating=4.8),
    ]


@pytest.mark.asyncio
async def test_ranker_rejects_unknown_sku() -> None:
    """§28 P8 instruction 3's exact required behaviour: any SKU the LLM
    names that is not in the supplied list is a hard rejection of the
    whole response, and it falls back to the deterministic scorer."""
    mandate = make_locked_mandate()
    candidates = _candidates()
    llm = ScriptedLLMClient(
        json_responses=[{"ranked_skus": ["HTL-A", "HTL-B", "HTL-DOES-NOT-EXIST"], "rationale": {}}]
    )
    result = await rank_candidates(llm, candidates, mandate)
    assert result.degraded is True
    assert [item.sku for item in result.items] == ["HTL-B", "HTL-A", "HTL-C"]  # price asc


@pytest.mark.asyncio
async def test_valid_llm_ranking_is_used_as_is() -> None:
    mandate = make_locked_mandate()
    candidates = _candidates()
    llm = ScriptedLLMClient(
        json_responses=[
            {"ranked_skus": ["HTL-C", "HTL-A", "HTL-B"], "rationale": {"HTL-C": "best rated"}}
        ]
    )
    result = await rank_candidates(llm, candidates, mandate)
    assert result.degraded is False
    assert [item.sku for item in result.items] == ["HTL-C", "HTL-A", "HTL-B"]
    assert result.rationale == {"HTL-C": "best rated"}


@pytest.mark.asyncio
async def test_llm_unavailable_falls_back_to_the_deterministic_scorer() -> None:
    mandate = make_locked_mandate()
    candidates = _candidates()
    result = await rank_candidates(AlwaysFailsLLMClient(), candidates, mandate)
    assert result.degraded is True
    assert [item.sku for item in result.items] == ["HTL-B", "HTL-A", "HTL-C"]


@pytest.mark.asyncio
async def test_a_ranking_missing_a_candidate_is_also_rejected() -> None:
    mandate = make_locked_mandate()
    candidates = _candidates()
    llm = ScriptedLLMClient(json_responses=[{"ranked_skus": ["HTL-A", "HTL-B"], "rationale": {}}])
    result = await rank_candidates(llm, candidates, mandate)
    assert result.degraded is True
    assert len(result.items) == 3


@pytest.mark.asyncio
async def test_infeasible_candidates_are_excluded_before_the_llm_ever_sees_them() -> None:
    mandate = make_locked_mandate()
    candidates = [
        *_candidates(),
        _item("HTL-OVER-BUDGET", unit_price_minor=9_000_000, rating=5.0),
    ]
    llm = ScriptedLLMClient(
        json_responses=[
            {
                "ranked_skus": ["HTL-A", "HTL-B", "HTL-C"],
                "rationale": {},
            }
        ]
    )
    result = await rank_candidates(llm, candidates, mandate)
    assert "HTL-OVER-BUDGET" not in [item.sku for item in result.items]


@pytest.mark.asyncio
async def test_llm_ranking_cannot_alter_price_or_rating_fields() -> None:
    """The LLM's own JSON never carries price/rating fields at all
    (schema is skus + rationale only) -- this proves the returned items
    are the exact original CatalogItem objects, not model-supplied data."""
    mandate = make_locked_mandate()
    candidates = _candidates()
    llm = ScriptedLLMClient(
        json_responses=[{"ranked_skus": ["HTL-C", "HTL-A", "HTL-B"], "rationale": {}}]
    )
    result = await rank_candidates(llm, candidates, mandate)
    by_sku = {item.sku: item for item in candidates}
    for item in result.items:
        assert item is by_sku[item.sku]
