"""§28 P5: explicit proof of transient-vs-terminal error classification --
"declines are never retried" is a guarantee that needs a call-count spy to
prove, not just an inference from reading _call_with_retry's `retry_on`
filter."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application.payment_service import (
    _call_with_retry,
    create_provider_order,
    reconcile_non_terminal_orders,
)
from actl.application.ports import (
    PaymentProvider,
    ProviderPayment,
    TerminalProviderError,
    TransientProviderError,
)
from actl.infrastructure.db.uow import UnitOfWork
from actl.infrastructure.providers.simulator.adapter import Scenario, SimulatorAdapter
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import FrozenClock, SystemClock
from actl.platform.ids import new_id
from tests.integration.payments.conftest import seed_purchase_fixture

pytestmark = pytest.mark.asyncio(loop_scope="session")


class _CallCountingProvider:
    """Wraps a real SimulatorAdapter, counting calls per method so a test
    can assert *how many times* the provider was actually reached."""

    def __init__(self, inner: SimulatorAdapter) -> None:
        self._inner = inner
        self.capture_call_count = 0

    async def create_order(self, *args: Any, **kwargs: Any) -> Any:
        return await self._inner.create_order(*args, **kwargs)

    async def fetch_payments(self, *args: Any, **kwargs: Any) -> list[ProviderPayment]:
        return await self._inner.fetch_payments(*args, **kwargs)

    async def capture(self, payment_id: str, amount_minor: int) -> ProviderPayment:
        self.capture_call_count += 1
        return await self._inner.capture(payment_id, amount_minor)

    async def refund(self, *args: Any, **kwargs: Any) -> Any:
        return await self._inner.refund(*args, **kwargs)

    def verify_checkout_signature(self, order_id: str, payment_id: str, signature: str) -> bool:
        return self._inner.verify_checkout_signature(order_id, payment_id, signature)

    def verify_webhook(self, raw_body: bytes, signature: str) -> bool:
        return self._inner.verify_webhook(raw_body, signature)


class _AlwaysTransientOnFetch:
    """A provider whose fetch_payments() always fails transiently — proves
    the reconciler treats a poll failure as "try again later", never as a
    decline. capture() asserts if called: the reconciler must never call
    it (it only reads state, never moves money)."""

    def __init__(self, inner: SimulatorAdapter) -> None:
        self._inner = inner

    async def create_order(self, *args: Any, **kwargs: Any) -> Any:
        return await self._inner.create_order(*args, **kwargs)

    async def fetch_payments(self, *args: Any, **kwargs: Any) -> list[ProviderPayment]:
        raise TransientProviderError("simulated poll timeout")

    async def capture(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("capture must never be called by the reconciler")

    async def refund(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def verify_checkout_signature(self, *args: Any, **kwargs: Any) -> bool:
        return False

    def verify_webhook(self, *args: Any, **kwargs: Any) -> bool:
        return False


async def test_decline_is_never_retried(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """§20 F2: "Terminal provider status -> Compensate... no blind
    retry." A DECLINE scenario's capture() raises TerminalProviderError on
    the first call — proven here to be called exactly once, never
    retried, regardless of settings.max_retry_attempts."""
    clock = SystemClock()
    inner = SimulatorAdapter(clock=clock, scenario=Scenario.DECLINE)
    counting = _CallCountingProvider(inner)
    breaker = CircuitBreaker(name="razorpay", clock=clock)

    order = await inner.create_order(100000, "INR", new_id("ik"), notes={})
    payments = await inner.fetch_payments(order.id)

    with pytest.raises(TerminalProviderError):
        await _call_with_retry(breaker, lambda: counting.capture(payments[0].id, 100000))

    assert counting.capture_call_count == 1


async def test_transient_failure_is_retried_until_success(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A TransientProviderError is retried (unlike a decline) until the
    configured retry budget succeeds."""
    clock = SystemClock()
    inner = SimulatorAdapter(
        clock=clock, scenario=Scenario.TRANSIENT_FAILURE, fail_before_success=2
    )
    breaker = CircuitBreaker(name="razorpay", clock=clock)

    order = await _call_with_retry(
        breaker, lambda: inner.create_order(100000, "INR", new_id("ik"), notes={})
    )
    assert order.status == "created"


async def test_reconciler_poll_failure_is_transient_and_leaves_order_non_terminal(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A transient fetch_payments() failure during reconciliation must
    never be mistaken for a decline — the order stays non-terminal so the
    *next* reconciler tick can try again, rather than being wrongly marked
    FAILED off a poll error."""
    clock = FrozenClock(at=SystemClock().now())
    fixture = await seed_purchase_fixture(session_factory, clock)
    inner = SimulatorAdapter(clock=clock)
    breaker = CircuitBreaker(name="razorpay", clock=clock)
    order_id = new_id("ord")

    order, _ = await create_provider_order(
        session_factory,
        inner,
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

    flaky: PaymentProvider = _AlwaysTransientOnFetch(inner)
    clock.advance(timedelta(seconds=100))
    flaky_breaker = CircuitBreaker(name="razorpay-flaky", clock=clock)
    async with UnitOfWork(session_factory) as uow:
        outcomes = await reconcile_non_terminal_orders(
            uow, flaky, clock, flaky_breaker, reconcile_after_s=45
        )

    matching = [o for o in outcomes if o.order_id == order_id]
    assert len(matching) == 1
    assert matching[0].action == "poll_failed"

    async with UnitOfWork(session_factory) as uow:
        after = await uow.orders.get(order_id)
    assert after is not None
    assert after.status == "CREATED"  # untouched -- not wrongly marked FAILED
