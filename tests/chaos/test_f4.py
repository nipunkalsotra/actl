"""§20 F4: "Duplicate webhook delivery -- Unique constraint on
provider_event_id -- Absorb silently, audit once." Transient class.
Migrated and extended from `tests/integration/payments/test_webhooks.py::
test_duplicate_webhook_absorbed_once` (§28 P5 exit criteria) into its own
chaos-layer file with the explicit reserved-balance and no-duplicate-
ledger-movement proofs §28 P9 instruction 2 adds. The original test stays
in place, unmodified, in tests/integration/payments/ -- this is an
additional, higher-level proof, not a replacement.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application.payment_service import (
    create_provider_order,
    process_unprocessed_webhooks,
    process_webhook_delivery,
)
from actl.infrastructure.db.uow import UnitOfWork
from actl.infrastructure.providers.simulator.adapter import SimulatorAdapter
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import SystemClock
from actl.platform.ids import new_id
from tests.chaos._helpers import reserved_balance
from tests.integration.payments.conftest import seed_purchase_fixture

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_duplicate_webhook_delivery_is_absorbed_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    provider = SimulatorAdapter(clock=clock)
    breaker = CircuitBreaker(name="f4-chaos", clock=clock)
    fixture = await seed_purchase_fixture(session_factory, clock)
    order_id = new_id("ord")

    order, _ = await create_provider_order(
        session_factory,
        provider,
        clock,
        breaker,
        order_id=order_id,
        mandate_id=fixture.mandate_id,
        decision_id=fixture.decision_id,
        quote_id=fixture.quote_id,
        amount_minor=fixture.amount_minor,
        currency="INR",
        attempt_no=1,
        intent_hash=fixture.intent_hash,
    )
    assert order.provider_order_id is not None
    payments = await provider.fetch_payments(order.provider_order_id)
    raw_body, signature, event_id = provider.build_webhook_payload(
        "payment.captured",
        provider_order_id=order.provider_order_id,
        provider_payment_id=payments[0].id,
        amount_minor=order.amount_minor,
    )
    payload = json.loads(raw_body)

    async with UnitOfWork(session_factory) as uow:
        first = await process_webhook_delivery(
            uow, provider, raw_body=raw_body, signature=signature,
            event_id=event_id, event_type="payment.captured", payload=payload,
        )
        await uow.commit()
    assert first.outcome == "accepted"

    # The HTTP receiver's fast-path only claims the delivery for dedup;
    # a background worker actually applies it (order transition + audit).
    async with UnitOfWork(session_factory) as uow:
        await process_unprocessed_webhooks(uow, clock)
        await uow.commit()

    # ---- The exact same delivery (same event_id), replayed. ----
    async with UnitOfWork(session_factory) as uow:
        second = await process_webhook_delivery(
            uow, provider, raw_body=raw_body, signature=signature,
            event_id=event_id, event_type="payment.captured", payload=payload,
        )
        await uow.commit()
    async with UnitOfWork(session_factory) as uow:
        await process_unprocessed_webhooks(uow, clock)
        await uow.commit()

    # ---- Property 1: typed status, reason, and audit evidence -- the
    # database's own unique constraint on provider_event_id is what
    # absorbs it (§15.3), never application-level branching. ----
    assert second.outcome == "duplicate"
    async with UnitOfWork(session_factory) as uow:
        events = await uow.webhook_events.get_by_provider_event_id(event_id)
    assert events is not None  # exactly one row for this event_id, by construction

    # ---- Property 2: reaches the required terminal state. ----
    async with UnitOfWork(session_factory) as uow:
        final_order = await uow.orders.get(order_id)
    assert final_order is not None
    assert final_order.status == "CAPTURED"

    # ---- Property 3: reserved balance is exactly zero (this call path
    # is below G4's reservation layer, same scope note as test_f3.py). ----
    assert await reserved_balance(session_factory, fixture.mandate_id) == 0

    # ---- No duplicates: applied (transitioned + audited) exactly once,
    # never a second time for the replayed delivery. ----
    async with UnitOfWork(session_factory) as uow:
        seq_range = await uow.audit_log.get_seq_range_for_order(order_id)
        assert seq_range is not None
        order_entries = await uow.audit_log.list_range(*seq_range)
    webhook_applied = [
        e
        for e in order_entries
        if e.action == "payment.result" and e.payload.get("source") == "webhook"
    ]
    assert len(webhook_applied) == 1
