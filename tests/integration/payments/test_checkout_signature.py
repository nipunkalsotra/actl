"""§28 P5 exit criteria: test_checkout_signature_verified_before_capture,
test_tampered_checkout_signature_declines_capture. §15.4: capture() must be
textually and *observably* unreachable unless verify_checkout_signature
returns True -- proved here with a spy that records every capture() call,
not just by reading the code path."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application.payment_service import (
    CheckoutSignatureInvalid,
    create_provider_order,
    verify_and_capture,
)
from actl.application.ports import ProviderPayment
from actl.infrastructure.db.uow import UnitOfWork
from actl.infrastructure.providers.simulator.adapter import SimulatorAdapter
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import SystemClock
from actl.platform.ids import new_id
from tests.integration.payments.conftest import seed_purchase_fixture

pytestmark = pytest.mark.asyncio(loop_scope="session")


class _CaptureSpy:
    """Wraps a real SimulatorAdapter, recording every capture() call so a
    test can assert it was *never* invoked -- proof of unreachability, not
    just an inference from reading verify_and_capture's source."""

    def __init__(self, inner: SimulatorAdapter) -> None:
        self._inner = inner
        self.capture_calls: list[tuple[str, int]] = []

    async def create_order(self, *args: Any, **kwargs: Any) -> Any:
        return await self._inner.create_order(*args, **kwargs)

    async def fetch_payments(self, *args: Any, **kwargs: Any) -> Any:
        return await self._inner.fetch_payments(*args, **kwargs)

    async def capture(self, payment_id: str, amount_minor: int) -> ProviderPayment:
        self.capture_calls.append((payment_id, amount_minor))
        return await self._inner.capture(payment_id, amount_minor)

    async def refund(self, *args: Any, **kwargs: Any) -> Any:
        return await self._inner.refund(*args, **kwargs)

    def verify_checkout_signature(self, order_id: str, payment_id: str, signature: str) -> bool:
        return self._inner.verify_checkout_signature(order_id, payment_id, signature)

    def verify_webhook(self, raw_body: bytes, signature: str) -> bool:
        return self._inner.verify_webhook(raw_body, signature)


async def _create_order_and_fetch_payment(
    session_factory: async_sessionmaker[AsyncSession],
    provider: SimulatorAdapter,
    clock: SystemClock,
    breaker: CircuitBreaker,
    fixture: Any,
) -> tuple[str, str, str]:
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
    return order_id, order.provider_order_id, payments[0].id


async def test_checkout_signature_verified_before_capture(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    fixture = await seed_purchase_fixture(session_factory, clock)
    inner = SimulatorAdapter(clock=clock)
    spy = _CaptureSpy(inner)
    breaker = CircuitBreaker(name="razorpay", clock=clock)

    order_id, provider_order_id, payment_id = await _create_order_and_fetch_payment(
        session_factory, inner, clock, breaker, fixture
    )
    valid_signature = inner.build_checkout_payload(provider_order_id, payment_id)

    async with UnitOfWork(session_factory) as uow:
        result = await verify_and_capture(
            uow,
            spy,  # type: ignore[arg-type]
            clock,
            breaker,
            order_id=order_id,
            provider_order_id=provider_order_id,
            provider_payment_id=payment_id,
            provider_signature=valid_signature,
            amount_minor=fixture.amount_minor,
        )

    assert result.status == "CAPTURED"
    assert spy.capture_calls == [(payment_id, fixture.amount_minor)]


async def test_tampered_checkout_signature_declines_capture(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    fixture = await seed_purchase_fixture(session_factory, clock)
    inner = SimulatorAdapter(clock=clock)
    spy = _CaptureSpy(inner)
    breaker = CircuitBreaker(name="razorpay", clock=clock)

    order_id, provider_order_id, payment_id = await _create_order_and_fetch_payment(
        session_factory, inner, clock, breaker, fixture
    )
    valid_signature = inner.build_checkout_payload(provider_order_id, payment_id)
    tampered = valid_signature[:-1] + ("0" if valid_signature[-1] != "0" else "1")

    async with UnitOfWork(session_factory) as uow:
        with pytest.raises(CheckoutSignatureInvalid):
            await verify_and_capture(
                uow,
                spy,  # type: ignore[arg-type]
                clock,
                breaker,
                order_id=order_id,
                provider_order_id=provider_order_id,
                provider_payment_id=payment_id,
                provider_signature=tampered,
                amount_minor=fixture.amount_minor,
            )

    assert spy.capture_calls == [], "capture() must never be called for a tampered signature"

    async with UnitOfWork(session_factory) as uow:
        order = await uow.orders.get(order_id)
    assert order is not None
    assert order.status == "FAILED"
    assert order.decline_reason == "invalid_checkout_signature"
