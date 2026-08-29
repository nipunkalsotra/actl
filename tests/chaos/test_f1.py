"""§20 F1: "Price changes between quote and order -- Gate G5:
catalog_version mismatch -- Auto re-quote once, re-evaluate policy,
proceed or deny with real numbers." Policy class.

The price change is injected via `CatalogRepository.mutate_price` --
the *same* out-of-band mutation `application/catalog_service.py`'s own
`mutate_price_demo_only` and `scripts/tamper.py`'s siblings use, called
directly against the repository, never through the
`POST /admin/catalog/{sku}/price` HTTP endpoint (§28 P9 instruction 1:
"F1 must use the required out-of-band price mutation, not the normal
catalog-admin endpoint, so the stale/tamper detection path is genuinely
tested" -- going through the endpoint would only prove the endpoint
works, not that a price change genuinely orphans an already-pinned quote).
This is the *only* bypass anywhere in this file: every propose attempt
below goes through `application.agents.merchant.handle_order_propose` --
the exact function real `order.propose` HTTP traffic is dispatched to
(`interfaces/agent/routes.py`) -- via `application.recovery.
propose_with_one_requote_on_stale_price`, which plays the buyer's own
client role for both attempts (see docs/adr/0010 decision 15).
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application.agents.merchant import handle_order_propose
from actl.application.catalog_service import create_quote
from actl.application.orchestrator import saga
from actl.application.recovery import _buyer_intent_hash, propose_with_one_requote_on_stale_price
from actl.domain.catalog.quote import Quote
from actl.domain.mandate.models import Mandate
from actl.domain.mandate.state_machine import MandateStatus
from actl.domain.policy.reason_codes import ReasonCode
from actl.infrastructure.db.repositories.catalog import CatalogItemRecord
from actl.infrastructure.db.uow import UnitOfWork
from actl.infrastructure.providers.simulator.adapter import SimulatorAdapter
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import SystemClock
from actl.platform.ids import new_id
from tests.chaos._helpers import build_mandate, reserved_balance, settled_balance

pytestmark = pytest.mark.asyncio(loop_scope="session")

SKU = "HTL-F1-CHAOS"


async def _seed_mandate_quote_and_mutate(
    session_factory: async_sessionmaker[AsyncSession], *, sku: str, actor_id: str
) -> tuple[Mandate, Quote, SystemClock]:
    mandate = build_mandate(max_price_delta_bps=1000)
    async with UnitOfWork(session_factory) as uow:
        await uow.mandates.add(mandate, MandateStatus.LOCKED)
        current_version = await uow.catalog.current_version()
        await uow.catalog.upsert_item(
            CatalogItemRecord(
                sku=sku,
                category="travel.hotel",
                merchant_id="mrc_f1",
                unit="night",
                unit_price_minor=280000,
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
                version=current_version,
            )
        )
        await uow.commit()

    clock = SystemClock()
    async with UnitOfWork(session_factory) as uow:
        pinned_quote = await create_quote(
            uow, clock, mandate_id=mandate.mandate_id, sku=sku, nights=3, actor_id=actor_id
        )
        await uow.commit()
    assert pinned_quote.unit_price_minor == 280000
    assert pinned_quote.total_minor == 840000

    # --- FAULT INJECTION: out-of-band price mutation, bypassing the
    # normal catalog-admin HTTP endpoint entirely. ---
    async with UnitOfWork(session_factory) as uow:
        mutated = await uow.catalog.mutate_price(sku, 292000)
        await uow.commit()
    assert mutated.unit_price_minor == 292000
    # 292000 * 3 = 876000, still within the mandate's 900000 cap -- the
    # re-quoted attempt is a genuine ALLOW, not a foregone DENY either way.

    return mandate, pinned_quote, clock


async def test_handle_order_propose_itself_detects_stale_price_after_the_mutation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Proves the *detection* lives in the real production entry point,
    with zero orchestration wrapper involved: builds the intent_hash
    exactly as a real buyer client would (`recovery._buyer_intent_hash`,
    over the quote's own pinned data) and calls `handle_order_propose`
    directly. If this ever regresses to `INTENT_MISMATCH` or a silent
    ALLOW, Gate G5/`catalog.freshness` has been defeated again (docs/
    adr/0010 decision 1) -- `propose_with_one_requote_on_stale_price`'s
    own retry logic would never even see the failure mode it exists to
    recover from."""
    actor_id = "agt_f1_direct"
    mandate, pinned_quote, clock = await _seed_mandate_quote_and_mutate(
        session_factory, sku="HTL-F1-DIRECT", actor_id=actor_id
    )
    provider = SimulatorAdapter(clock=clock)
    breaker = CircuitBreaker(name="f1-direct", clock=clock)
    trace_id = new_id("trc")

    assert pinned_quote.quote_hash is not None
    intent_hash = await _buyer_intent_hash(session_factory, mandate, pinned_quote)
    outcome = await handle_order_propose(
        session_factory,
        provider,
        clock,
        breaker,
        quote_id=pinned_quote.quote_id,
        quote_hash=pinned_quote.quote_hash,
        mandate_id=mandate.mandate_id,
        mandate_spec_hash=mandate.spec_hash or "",
        intent_hash=intent_hash,
        trace_id=trace_id,
        actor_id=actor_id,
    )

    assert outcome.body.get("decision") == "reject", outcome.body
    assert outcome.body.get("reason_code") == str(ReasonCode.STALE_PRICE), outcome.body
    assert await reserved_balance(session_factory, mandate.mandate_id) == 0


async def test_stale_price_is_detected_requoted_once_and_settles(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # §20.1's own worked example treats a ~4% re-quoted shift (280000 ->
    # 292000) as acceptable -- max_price_delta_bps=1000 (10%) isolates
    # STALE_PRICE (catalog_version mismatch, F1's actual detection
    # mechanism) from PRICE_DRIFT (a separate rule, zero-tolerance in the
    # shared make_locked_mandate() fixture, not this failure mode's
    # concern). version=current_version() at seed time -- same root cause
    # as docs/adr/0009 decision 14.
    actor_id = "agt_f1_chaos"
    trace_id = new_id("trc")
    mandate, pinned_quote, clock = await _seed_mandate_quote_and_mutate(
        session_factory, sku=SKU, actor_id=actor_id
    )

    provider = SimulatorAdapter(clock=clock)
    breaker = CircuitBreaker(name="f1-chaos", clock=clock)

    outcome = await propose_with_one_requote_on_stale_price(
        session_factory,
        provider,
        clock,
        breaker,
        mandate=mandate,
        quote=pinned_quote,
        actor_id=actor_id,
        trace_id=trace_id,
    )

    # ---- Property 1: detected with the correct typed status, reason,
    # and audit evidence (both decisions -- the stale reject and the
    # requoted accept -- preserved in the chain under one trace_id). ----
    assert outcome.requoted is True
    assert outcome.result.verdict == "ALLOW", outcome.result
    assert outcome.final_quote.unit_price_minor == 292000
    assert outcome.final_quote.total_minor == 876000

    async with UnitOfWork(session_factory) as uow:
        entries = await uow.audit_log.get_by_trace_id(trace_id)
    proposed_entries = [e for e in entries if e.action == "order.proposed"]
    assert len(proposed_entries) == 2, "both the stale and the requoted decision must be audited"
    assert proposed_entries[0].payload["reason_codes"] == ["STALE_PRICE"]
    assert proposed_entries[0].payload["verdict"] == "DENY"
    assert proposed_entries[1].payload["verdict"] == "ALLOW"

    # ---- Property 2: reaches the required terminal state (settled). ----
    assert outcome.result.order_id is not None
    assert outcome.saga_id is not None
    order_id = outcome.result.order_id
    async with UnitOfWork(session_factory) as uow:
        order = await uow.orders.get(order_id)
    assert order is not None and order.provider_order_id is not None
    payments = await provider.fetch_payments(order.provider_order_id)
    payment = payments[0]
    signature = provider.build_checkout_payload(order.provider_order_id, payment.id)
    result = await saga.complete_purchase(
        outcome.saga_id, session_factory, provider, clock, breaker,
        provider_order_id=order.provider_order_id, provider_payment_id=payment.id,
        provider_signature=signature, actor_id=actor_id,
    )
    assert result.status == "COMPLETED"
    async with UnitOfWork(session_factory) as uow:
        final_order = await uow.orders.get(order_id)
    assert final_order is not None
    assert final_order.status == "CAPTURED"
    assert final_order.amount_minor == 876000  # the re-quoted, real amount -- not the stale one

    # ---- Property 3: reserved ledger balance returns to exactly zero
    # (the stale attempt never reached G4 at all -- caught by the policy
    # pre-check before any reservation -- and the successful attempt's
    # own reservation has fully moved to settled). ----
    assert await reserved_balance(session_factory, mandate.mandate_id) == 0
    assert await settled_balance(session_factory, mandate.mandate_id) == 876000

    # ---- No duplicates: exactly one order, one settlement. ----
    # (settlement.closed is written under complete_purchase's own fresh
    # trace_id, not the propose-time one -- §22's correlation model ties
    # a checkout/settlement callback to its own trace, matching a real,
    # separate browser callback moment; found here by order_id instead.)
    async with UnitOfWork(session_factory) as uow:
        seq_range = await uow.audit_log.get_seq_range_for_order(order_id)
        assert seq_range is not None
        order_entries = await uow.audit_log.list_range(*seq_range)
    settlement_entries = [
        e
        for e in order_entries
        if e.action == "settlement.closed" and e.subject.get("order_id") == order_id
    ]
    assert len(settlement_entries) == 1
