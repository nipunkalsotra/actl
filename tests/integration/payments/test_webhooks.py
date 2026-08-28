"""§28 P5 exit criteria: test_duplicate_webhook_absorbed_once,
test_invalid_signature_rejected_and_not_processed. §15.3: duplicates
absorbed at the database (unique constraint on provider_event_id), never
in application logic; a signature failure is dropped, never processed."""

from __future__ import annotations

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application.payment_service import (
    create_provider_order,
    process_unprocessed_webhooks,
    process_webhook_delivery,
)
from actl.infrastructure.db.repositories.orders import OrderRecord
from actl.infrastructure.db.uow import UnitOfWork
from actl.infrastructure.providers.simulator.adapter import SimulatorAdapter
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import Clock, SystemClock
from actl.platform.ids import new_id
from tests.integration.payments.conftest import seed_purchase_fixture

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _seed_order(
    session_factory: async_sessionmaker[AsyncSession],
    provider: SimulatorAdapter,
    clock: Clock,
    breaker: CircuitBreaker,
) -> tuple[str, OrderRecord]:
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
    return order_id, order


async def test_duplicate_webhook_absorbed_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    provider = SimulatorAdapter(clock=clock)
    breaker = CircuitBreaker(name="razorpay", clock=clock)
    order_id, order = await _seed_order(session_factory, provider, clock, breaker)

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
            uow,
            provider,
            raw_body=raw_body,
            signature=signature,
            event_id=event_id,
            event_type="payment.captured",
            payload=payload,
        )
    async with UnitOfWork(session_factory) as uow:
        second = await process_webhook_delivery(
            uow,
            provider,
            raw_body=raw_body,
            signature=signature,
            event_id=event_id,
            event_type="payment.captured",
            payload=payload,
        )
    # a third, truly concurrent-style delivery to prove DB-level absorption,
    # not just "we happened to check first"
    async with UnitOfWork(session_factory) as uow:
        third = await process_webhook_delivery(
            uow,
            provider,
            raw_body=raw_body,
            signature=signature,
            event_id=event_id,
            event_type="payment.captured",
            payload=payload,
        )

    assert first.outcome == "accepted"
    assert second.outcome == "duplicate"
    assert third.outcome == "duplicate"

    async with UnitOfWork(session_factory) as uow:
        processed = await process_unprocessed_webhooks(uow, clock)
    # applied exactly once, regardless of 3 deliveries -- other tests in this
    # shared-Postgres session may also have left unprocessed events behind,
    # so this checks *our* event's count, not exact list equality
    assert processed.count(event_id) == 1

    async with UnitOfWork(session_factory) as uow:
        final_order = await uow.orders.get(order_id)
    assert final_order is not None
    assert final_order.status == "CAPTURED"

    # a second worker pass over the now-already-processed event must be a no-op
    async with UnitOfWork(session_factory) as uow:
        second_pass = await process_unprocessed_webhooks(uow, clock)
    assert second_pass == []


async def test_invalid_signature_rejected_and_not_processed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    provider = SimulatorAdapter(clock=clock)
    breaker = CircuitBreaker(name="razorpay", clock=clock)
    order_id, order = await _seed_order(session_factory, provider, clock, breaker)

    payments = await provider.fetch_payments(order.provider_order_id)
    raw_body, _valid_signature, event_id = provider.build_webhook_payload(
        "payment.captured",
        provider_order_id=order.provider_order_id,
        provider_payment_id=payments[0].id,
        amount_minor=order.amount_minor,
    )
    payload = json.loads(raw_body)
    forged_signature = "0" * 64

    async with UnitOfWork(session_factory) as uow:
        receipt = await process_webhook_delivery(
            uow,
            provider,
            raw_body=raw_body,
            signature=forged_signature,
            event_id=event_id,
            event_type="payment.captured",
            payload=payload,
        )
    assert receipt.outcome == "invalid_signature"

    async with UnitOfWork(session_factory) as uow:
        stored = await uow.webhook_events.get_by_provider_event_id(event_id)
    assert stored is None  # nothing persisted for an invalid signature (ADR 0006 decision 13)

    async with UnitOfWork(session_factory) as uow:
        processed = await process_unprocessed_webhooks(uow, clock)
    assert event_id not in processed  # never handed to the worker's apply step

    async with UnitOfWork(session_factory) as uow:
        final_order = await uow.orders.get(order_id)
    assert final_order is not None
    assert final_order.status == "CREATED"  # untouched by the forged delivery
