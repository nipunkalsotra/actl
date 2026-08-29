"""§28 P8 exit criteria: test_llm_call_budget_never_exceeds_3. §17.3's
hard ceiling of 3 LLM calls per transaction, shared across U1/U2/U3 via
`BudgetedLLMClient` -- proven under the worst case (every call fails
schema validation, driving both U1's and U2's 2-attempt repair loops to
their limit).
"""

from __future__ import annotations

import pytest

from actl.application.conversation.budget import BudgetedLLMClient
from actl.application.conversation.extraction import extract_mandate_draft
from actl.application.conversation.narration import narrate_entry
from actl.application.conversation.ranking import rank_candidates
from actl.application.ports import LLMUnavailable
from actl.domain.catalog.models import (
    CatalogAttributes,
    CatalogItem,
    CatalogLocation,
    CatalogPolicy,
)
from actl.domain.mandate.draft import ClarificationNeeded
from actl.infrastructure.db.repositories.audit_log import AuditLogRecord
from tests.integration.db.conftest import make_locked_mandate
from tests.support.fake_llm_client import ScriptedLLMClient


def _item(sku: str) -> CatalogItem:
    return CatalogItem(
        sku=sku,
        category="travel.hotel",
        merchant_id="mrc_test",
        unit="night",
        unit_price_minor=200000,
        available_units=5,
        location=CatalogLocation(city="Goa", country="IN"),
        attributes=CatalogAttributes(rating=4.0, sea_facing=True, breakfast_included=True),
        policy=CatalogPolicy(
            refundable=True, cancellation_window_h=48, instant_confirm=True, taxes_included=True
        ),
        version=1,
        quote_required=True,
    )


@pytest.mark.asyncio
async def test_budgeted_client_refuses_a_call_beyond_the_ceiling() -> None:
    inner = ScriptedLLMClient(json_responses=[{"a": 1}, {"a": 2}])
    budget = BudgetedLLMClient(inner=inner, max_calls=1)
    await budget.complete_json(system="s", user="u", max_tokens=10)
    assert budget.calls_made == 1
    with pytest.raises(LLMUnavailable):
        await budget.complete_json(system="s", user="u", max_tokens=10)
    assert budget.calls_made == 1  # the refused call never reached `inner`
    assert len(inner.json_calls) == 1


@pytest.mark.asyncio
async def test_llm_call_budget_never_exceeds_3() -> None:
    """§28 P8 exit criteria's exact required test. Worst case: U1's
    schema-repair loop spends 2 calls on malformed JSON, leaving only 1
    of the shared 3-call budget for U2 -- U2's own repair loop would want
    2, but the budget denies the second, so U2 safely falls back to the
    deterministic scorer instead of ever making a 3rd *network* call, and
    U3 narration finds the budget already spent and returns no narration.
    Not one LLM failure here authorizes or blocks anything: extraction
    falls back to asking every question, ranking falls back to the
    deterministic price/rating order, narration is simply skipped."""
    inner = ScriptedLLMClient(
        json_responses=[
            {"nights": "not-a-number"},  # U1 attempt 1: invalid
            {"nights": "still-not-a-number"},  # U1 attempt 2 (repair): invalid, budget now at 2
            {"ranked_skus": ["HTL-A"]},  # U2 attempt 1: would succeed, but budget denies it first
        ]
    )
    budget = BudgetedLLMClient(inner=inner, max_calls=3)

    mandate = make_locked_mandate()
    candidates = [_item("HTL-A"), _item("HTL-B")]
    entry = AuditLogRecord(
        trace_id="trc_budget_test",
        actor_type="agent",
        actor_id="agt_test",
        action="quote.issued",
        subject={},
        payload={},
        payload_hash="sha256:" + "0" * 64,
        prev_hash="sha256:" + "0" * 64,
        entry_hash="sha256:" + "1" * 64,
        seq=1,
    )

    extraction_result = await extract_mandate_draft(budget, "book me something nice in Goa")
    ranking_result = await rank_candidates(budget, candidates, mandate)
    narration_result = await narrate_entry(budget, entry)

    assert budget.calls_made <= 3
    assert len(inner.json_calls) <= 3
    assert isinstance(extraction_result, ClarificationNeeded)  # safe fallback, not a crash
    assert ranking_result.degraded is True  # safe fallback, not a crash
    assert [i.sku for i in ranking_result.items] == ["HTL-A", "HTL-B"]  # deterministic order intact
    assert narration_result is None  # safe fallback, not a crash
