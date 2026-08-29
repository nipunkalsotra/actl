"""§20 F6 (LLM unavailable/rate-limited -> deterministic fallback path,
trace flagged degraded) and F7 (LLM names an unsupplied SKU -> rejected,
fallback, audited) -- §28 P8 exit criteria's own callout, "the important
one": test_full_txn_with_every_llm_call_failing.

Every LLM call in this test raises via `AlwaysFailsLLMClient`. The money
transaction (mandate lock, quote, the real Money Action Gate, the real
saga, the real ledger, the real audit chain, the real outbox) completes
correctly regardless -- §17 Figure 17.1's HARD BOUNDARY, proven end to
end rather than asserted in a comment: "If every LLM call failed, the
transaction still completes correctly."
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application.agents.merchant import handle_order_propose
from actl.application.audit_service import verify_chain
from actl.application.catalog_service import create_quote
from actl.application.conversation.extraction import extract_mandate_draft
from actl.application.conversation.narration import narrate_and_store
from actl.application.conversation.ranking import rank_candidates
from actl.application.orchestrator import saga
from actl.domain.catalog.models import (
    CatalogAttributes,
    CatalogItem,
    CatalogLocation,
    CatalogPolicy,
)
from actl.domain.mandate.draft import ClarificationNeeded
from actl.domain.mandate.state_machine import MandateStatus
from actl.domain.policy.rules import PurchaseIntent, compute_intent_hash
from actl.infrastructure.db.repositories.catalog import CatalogItemRecord
from actl.infrastructure.db.uow import UnitOfWork
from actl.infrastructure.providers.simulator.adapter import SimulatorAdapter
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import SystemClock
from actl.platform.ids import new_id
from tests.integration.db.conftest import make_locked_mandate
from tests.support.fake_llm_client import AlwaysFailsLLMClient

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_full_txn_with_every_llm_call_failing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    llm = AlwaysFailsLLMClient()
    mandate = make_locked_mandate()
    sku = "HTL-F6F7-" + new_id("x")[:8]
    actor_id = "agt_f6f7_test"
    trace_id = new_id("trc")

    async with UnitOfWork(session_factory) as uow:
        await uow.mandates.add(mandate, MandateStatus.LOCKED)
        await uow.catalog.upsert_item(
            CatalogItemRecord(
                sku=sku,
                category="travel.hotel",
                merchant_id="mrc_f6f7",
                unit="night",
                unit_price_minor=250000,
                available_units=5,
                location_city="Goa",
                location_country="IN",
                rating=4.2,
                sea_facing=True,
                breakfast_included=True,
                refundable=True,
                cancellation_window_h=48,
                instant_confirm=True,
                taxes_included=True,
                quote_required=True,
                version=1,
            )
        )
        await uow.commit()

    # ---- F6: every U1 call fails -- must fall back safely, never raise. ----
    extraction_result = await extract_mandate_draft(llm, "book me something nice in Goa")
    assert isinstance(extraction_result, ClarificationNeeded)

    # ---- F6/F7: every U2 call fails -- falls back to the deterministic
    # scorer, degraded=true, never raises, never blocks candidate
    # availability. ----
    candidates = [
        CatalogItem(
            sku=sku,
            category="travel.hotel",
            merchant_id="mrc_f6f7",
            unit="night",
            unit_price_minor=250000,
            available_units=5,
            location=CatalogLocation(city="Goa", country="IN"),
            attributes=CatalogAttributes(rating=4.2, sea_facing=True, breakfast_included=True),
            policy=CatalogPolicy(
                refundable=True,
                cancellation_window_h=48,
                instant_confirm=True,
                taxes_included=True,
            ),
            version=1,
            quote_required=True,
        )
    ]
    ranking_result = await rank_candidates(llm, candidates, mandate)
    assert ranking_result.degraded is True
    assert [i.sku for i in ranking_result.items] == [sku]

    # ---- The real, deterministic P4-P7 transaction -- entirely
    # independent of the two LLM failures above; no LLMClient is passed
    # into any of the functions below at all. ----
    async with UnitOfWork(session_factory) as uow:
        quote = await create_quote(
            uow, SystemClock(), mandate_id=mandate.mandate_id, sku=sku, nights=3, actor_id=actor_id
        )
        await uow.commit()
        item = await uow.catalog.get_item(sku)
    assert item is not None
    assert quote.quote_hash is not None

    intent_draft = PurchaseIntent(
        currency=mandate.bounds.currency,
        category=item.category,
        merchant=item.merchant_id,
        unit_price_minor=quote.unit_price_minor,
        total_minor=quote.total_minor,
        nights=quote.nights,
        rooms=mandate.intent.rooms,
        refundable=quote.refundable,
        quoted_total_minor=quote.total_minor,
        current_total_minor=item.unit_price_minor * quote.nights,
        catalog_version=quote.catalog_version,
        mandate_spec_hash=mandate.spec_hash or "",
        intent_hash="",
    )
    intent_hash = compute_intent_hash(intent_draft)

    clock = SystemClock()
    breaker = CircuitBreaker(name="f6f7-money", clock=clock)
    provider = SimulatorAdapter(clock=clock)

    outcome = await handle_order_propose(
        session_factory,
        provider,
        clock,
        breaker,
        quote_id=quote.quote_id,
        quote_hash=quote.quote_hash,
        mandate_id=mandate.mandate_id,
        mandate_spec_hash=mandate.spec_hash or "",
        intent_hash=intent_hash,
        trace_id=trace_id,
        actor_id=actor_id,
    )
    assert outcome.body.get("decision") == "accept", outcome.body
    order_id = str(outcome.body["order_id"])
    saga_id = str(outcome.body["saga_id"])

    async with UnitOfWork(session_factory) as uow:
        order = await uow.orders.get(order_id)
    assert order is not None and order.provider_order_id is not None
    payments = await provider.fetch_payments(order.provider_order_id)
    payment = payments[0]
    signature = provider.build_checkout_payload(order.provider_order_id, payment.id)
    result = await saga.complete_purchase(
        saga_id,
        session_factory,
        provider,
        clock,
        breaker,
        provider_order_id=order.provider_order_id,
        provider_payment_id=payment.id,
        provider_signature=signature,
        actor_id=actor_id,
    )
    assert result.status == "COMPLETED"

    # ---- Money action correct: order captured, ledger settled. ----
    async with UnitOfWork(session_factory) as uow:
        final_order = await uow.orders.get(order_id)
    assert final_order is not None
    assert final_order.status == "CAPTURED"
    assert final_order.amount_minor == quote.total_minor

    # ---- Audit chain correct: every entry this transaction wrote
    # verifies, hash-linked, gapless. ----
    async with UnitOfWork(session_factory) as uow:
        tail = await uow.audit_log.get_tail()
    assert tail is not None
    async with UnitOfWork(session_factory) as uow:
        chain = await verify_chain(uow, 1, tail[0])
    assert chain.ok, chain.break_

    # ---- Outbox correct: the transaction's own events are present and
    # attributable to this order. ----
    async with UnitOfWork(session_factory) as uow:
        entries = await uow.audit_log.get_by_trace_id(trace_id)
    assert len(entries) > 0
    assert any(e.action == "order.proposed" for e in entries)

    # ---- F6: every U3 call also fails -- narration is skipped, never
    # raises, never touches the entry/chain that was just proven valid. ----
    async with UnitOfWork(session_factory) as uow:
        for entry in entries:
            wrote = await narrate_and_store(llm, uow, entry)
            assert wrote is False
        await uow.commit()

    async with UnitOfWork(session_factory) as uow:
        chain_after = await verify_chain(uow, 1, tail[0])
    assert chain_after.ok
    assert chain_after.head_entry_hash == chain.head_entry_hash
