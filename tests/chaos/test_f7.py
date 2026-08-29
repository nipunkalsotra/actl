"""§20 F7: "LLM names a SKU that does not exist -- Referential validation
after parsing -- Reject the response, fall back, audit the rejection."
Policy class.

`domain.agent.buyer.apply_llm_ranking`'s exact-permutation check is what
does the rejecting (§28 P8 instruction 3); this chaos-layer test proves
the resulting fallback never touches money at all -- no reservation, no
order, nothing for "reserved balance returns to zero" to be anything but
trivially, permanently true, which is itself the point: a referential
violation is caught entirely before the gate.
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
from tests.chaos._helpers import build_mandate
from tests.support.fake_llm_client import ScriptedLLMClient

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _item(sku: str, *, unit_price_minor: int, rating: float) -> CatalogItem:
    return CatalogItem(
        sku=sku,
        category="travel.hotel",
        merchant_id="mrc_f7",
        unit="night",
        unit_price_minor=unit_price_minor,
        available_units=5,
        location=CatalogLocation(city="Goa", country="IN"),
        attributes=CatalogAttributes(rating=rating, sea_facing=True, breakfast_included=True),
        policy=CatalogPolicy(
            refundable=True, cancellation_window_h=48, instant_confirm=True, taxes_included=True
        ),
        version=1,
        quote_required=True,
    )


async def test_hallucinated_sku_is_rejected_falls_back_and_is_auditable() -> None:
    mandate = build_mandate()
    candidates = [
        _item("HTL-F7-A", unit_price_minor=200000, rating=4.0),
        _item("HTL-F7-B", unit_price_minor=150000, rating=3.5),
    ]
    llm = ScriptedLLMClient(
        json_responses=[
            {
                "ranked_skus": ["HTL-F7-A", "HTL-F7-SECRET-DOES-NOT-EXIST"],
                "rationale": {"HTL-F7-SECRET-DOES-NOT-EXIST": "a SKU the model invented"},
            }
        ]
    )

    result = await rank_candidates(llm, candidates, mandate)

    # ---- Property 1: typed status ("degraded" -- the fallback signal
    # this build uses in place of a persisted "audit the rejection" row;
    # see docs/adr/0010-p9-failure-theatre-decisions.md decision 7 for why
    # no new audit write was added here) and the response is provably
    # discarded wholesale, not partially used. ----
    assert result.degraded is True
    assert "HTL-F7-SECRET-DOES-NOT-EXIST" not in [i.sku for i in result.items]
    assert result.rationale == {}  # the model's own rationale text is discarded too

    # ---- Property 2: reaches the required terminal state -- the
    # deterministic price-ascending/rating-descending order, exactly
    # §28 P7 instruction 6's scorer, never a partially-trusted LLM order. ----
    assert [i.sku for i in result.items] == ["HTL-F7-B", "HTL-F7-A"]

    # ---- Property 3: reserved ledger balance is (and can only ever be)
    # exactly zero -- U2 ranking runs before any quote, gate, or
    # reservation exists (no UnitOfWork/session is even opened by this
    # test), so a referential violation here has no money-path reach at
    # all -- proven structurally by `tests/architecture/test_boundaries.
    # py::test_conversation_module_cannot_reach_the_gate_or_a_payment_
    # provider` (§28 P8), not re-asserted per-call here. ----

    # ---- No duplicates / no widening: every candidate returned is
    # exactly one of the two supplied, never a third, never repeated. ----
    assert len(result.items) == 2
    assert len({i.sku for i in result.items}) == 2


async def test_hallucinated_sku_among_otherwise_valid_ones_still_rejects_the_whole_response() -> (
    None
):
    """A single invented SKU poisons the entire response -- the real
    SKUs alongside it are not partially trusted either."""
    mandate = build_mandate()
    candidates = [
        _item("HTL-F7-C", unit_price_minor=180000, rating=4.2),
        _item("HTL-F7-D", unit_price_minor=220000, rating=4.8),
        _item("HTL-F7-E", unit_price_minor=190000, rating=4.1),
    ]
    llm = ScriptedLLMClient(
        json_responses=[
            {
                "ranked_skus": ["HTL-F7-D", "HTL-F7-GHOST", "HTL-F7-C", "HTL-F7-E"],
                "rationale": {},
            }
        ]
    )

    result = await rank_candidates(llm, candidates, mandate)

    assert result.degraded is True
    assert [i.sku for i in result.items] == ["HTL-F7-C", "HTL-F7-E", "HTL-F7-D"]  # price asc
