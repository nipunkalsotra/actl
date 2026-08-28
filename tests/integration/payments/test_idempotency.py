"""§28 P5 exit criteria: test_idempotent_retry_creates_one_order. §15.2:
`INSERT ... ON CONFLICT (key) DO NOTHING` — real-Postgres proof that
competing requests never create duplicate provider orders or duplicate
local order rows."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application.payment_service import (
    IdempotentAttemptFailed,
    create_provider_order,
)
from actl.infrastructure.db.uow import UnitOfWork
from actl.infrastructure.providers.simulator.adapter import Scenario, SimulatorAdapter
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import SystemClock
from actl.platform.ids import new_id
from actl.platform.retry import RetryExhausted
from tests.integration.payments.conftest import seed_purchase_fixture

pytestmark = pytest.mark.asyncio(loop_scope="session")

N = 10


async def test_idempotent_retry_creates_one_order(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    fixture = await seed_purchase_fixture(session_factory, clock)
    provider = SimulatorAdapter(clock=clock)
    breaker = CircuitBreaker(name="razorpay", clock=clock)
    order_id = new_id("ord")

    async def _one_attempt() -> tuple[str | None, bool]:
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
        return order.provider_order_id, was_duplicate

    results = await asyncio.gather(*(_one_attempt() for _ in range(N)))

    provider_order_ids = {r[0] for r in results}
    assert len(provider_order_ids) == 1, (
        f"expected exactly one provider order, got {provider_order_ids}"
    )
    assert None not in provider_order_ids

    duplicate_flags = [r[1] for r in results]
    assert duplicate_flags.count(False) == 1  # exactly one non-duplicate winner
    assert duplicate_flags.count(True) == N - 1

    async with UnitOfWork(session_factory) as uow:
        order = await uow.orders.get(order_id)
    assert order is not None
    assert order.provider_order_id == provider_order_ids.pop()


async def test_replaying_a_terminally_failed_attempt_returns_the_same_failure(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """§15.2: a replay of a failed attempt returns the same failure — it
    never silently retries the provider. A genuinely new attempt needs a
    new attempt_no (a new key), never a replay of the old one."""
    clock = SystemClock()
    fixture = await seed_purchase_fixture(session_factory, clock)
    provider = SimulatorAdapter(
        clock=clock, scenario=Scenario.TRANSIENT_FAILURE, fail_before_success=999
    )
    breaker = CircuitBreaker(name="razorpay", clock=clock, failure_threshold=999)
    order_id = new_id("ord")

    with pytest.raises(RetryExhausted):
        await create_provider_order(
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

    with pytest.raises(IdempotentAttemptFailed):
        await create_provider_order(
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
