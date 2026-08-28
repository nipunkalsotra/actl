"""§20 F3 (webhook never arrives), F5 (provider timeout on order creation)
-- §28 P5 exit criteria: test_missing_webhook_recovered_by_reconciler,
test_timeout_retried_with_same_key. F4 (duplicate webhook delivery) is
exercised by test_duplicate_webhook_absorbed_once in
tests/integration/payments/test_webhooks.py -- both directories run
together per the doc's own exit command
(`pytest tests/integration/payments tests/chaos/test_f3_f4_f5.py -q`).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application.payment_service import (
    create_provider_order,
    reconcile_non_terminal_orders,
)
from actl.infrastructure.db.uow import UnitOfWork
from actl.infrastructure.providers.simulator.adapter import Scenario, SimulatorAdapter
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import FrozenClock, SystemClock
from actl.platform.ids import new_id
from tests.integration.payments.conftest import seed_purchase_fixture

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_missing_webhook_recovered_by_reconciler(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """§20 F3: "Reconciler finds a non-terminal order past threshold ->
    Poll provider, settle from the polled state." No webhook is ever
    delivered for this order — capture() is called directly against the
    provider (as auto-capture or an out-of-band Checkout success would
    leave it), simulating exactly the webhook that never arrived."""
    clock = FrozenClock(at=SystemClock().now())
    fixture = await seed_purchase_fixture(session_factory, clock)
    provider = SimulatorAdapter(clock=clock)
    breaker = CircuitBreaker(name="razorpay", clock=clock)
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
    await provider.capture(payments[0].id, fixture.amount_minor)  # no webhook ever sent for this

    async with UnitOfWork(session_factory) as uow:
        before = await uow.orders.get(order_id)
    assert before is not None
    assert before.status == "CREATED"  # our own record has no idea yet

    clock.advance(timedelta(seconds=100))
    async with UnitOfWork(session_factory) as uow:
        outcomes = await reconcile_non_terminal_orders(
            uow, provider, clock, breaker, reconcile_after_s=45
        )

    matching = [o for o in outcomes if o.order_id == order_id]
    assert len(matching) == 1
    assert matching[0].action == "captured"

    async with UnitOfWork(session_factory) as uow:
        after = await uow.orders.get(order_id)
    assert after is not None
    assert after.status == "CAPTURED"
    assert after.provider_payment_id == payments[0].id


async def test_timeout_retried_with_same_key(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """§20 F5: "Retry with the same idempotency key; never a second
    order." Two transient timeouts, then success -- one create_provider_order
    call handles all three attempts internally (§28 P5 instruction 2:
    retry classification from the platform layer)."""
    clock = SystemClock()
    fixture = await seed_purchase_fixture(session_factory, clock)
    provider = SimulatorAdapter(
        clock=clock, scenario=Scenario.TRANSIENT_FAILURE, fail_before_success=2
    )
    breaker = CircuitBreaker(name="razorpay", clock=clock)
    order_id = new_id("ord")

    order, was_duplicate = await create_provider_order(
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

    assert not was_duplicate
    assert order.status == "CREATED"
    assert order.provider_order_id is not None
    assert len(provider._orders) == 1  # never a second order despite two prior timeouts

    async with UnitOfWork(session_factory) as uow:
        stored = await uow.orders.get_by_idempotency_key(order.idempotency_key)
    assert stored is not None
    assert stored.id == order_id  # the same idempotency key covered every attempt
