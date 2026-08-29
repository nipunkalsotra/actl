"""§20 F5: "Provider timeout on order creation -- Client timeout -- Retry
with the same idempotency key; never a second order." Transient class.
Driven through the *full* gate (`saga.begin_purchase`) rather than
`create_provider_order` directly, unlike this failure mode's original P5
test (`test_timeout_retried_with_same_key`, kept unmodified in the
combined tests/chaos/test_f3_f4_f5.py's replacement) -- so a real G4
reservation exists and "reserved ledger balance returns to exactly zero"
is a non-trivial proof (moved to settled after checkout completes), not
a vacuous one.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application.gate import MoneyActionRequest
from actl.application.orchestrator import saga
from actl.infrastructure.db.uow import UnitOfWork
from actl.infrastructure.providers.simulator.adapter import Scenario, SimulatorAdapter
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import SystemClock
from actl.platform.ids import new_id
from tests.chaos._helpers import reserved_balance, settled_balance
from tests.integration.gate.conftest import GateFixture, seed_valid_gate_fixture

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _req(fixture: GateFixture) -> MoneyActionRequest:
    return MoneyActionRequest(
        trace_id=new_id("trc"),
        mandate_id=fixture.mandate.mandate_id,
        decision_id=fixture.decision_id,
        quote_id=fixture.quote_id,
        intent_hash=fixture.intent_hash,
        amount_minor=fixture.amount_minor,
        currency="INR",
        attempt_no=1,
    )


async def test_transient_timeout_is_retried_with_the_same_key_never_a_second_order(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    # Two transient timeouts, then success -- create_provider_order's own
    # internal retry-with-backoff (§28 P5) handles all three attempts
    # inside one saga.begin_purchase call.
    provider = SimulatorAdapter(
        clock=clock, scenario=Scenario.TRANSIENT_FAILURE, fail_before_success=2
    )
    breaker = CircuitBreaker(name="f5-chaos", clock=clock)
    fixture = await seed_valid_gate_fixture(session_factory, clock)

    begin = await saga.begin_purchase(_req(fixture), session_factory, provider, clock, breaker)

    # ---- Property 1: typed status, reason, and audit evidence. ----
    assert begin.status == "AWAITING_AUTHORIZATION"
    assert begin.order_id is not None
    assert len(provider._orders) == 1  # never a second order despite two prior timeouts
    async with UnitOfWork(session_factory) as uow:
        order = await uow.orders.get(begin.order_id)
    assert order is not None
    async with UnitOfWork(session_factory) as uow:
        stored = await uow.orders.get_by_idempotency_key(order.idempotency_key)
    assert stored is not None
    assert stored.id == begin.order_id  # the same idempotency key covered every attempt
    async with UnitOfWork(session_factory) as uow:
        seq_range = await uow.audit_log.get_seq_range_for_order(begin.order_id)
        assert seq_range is not None
        order_entries = await uow.audit_log.list_range(*seq_range)
    assert any(e.action == "payment.intent" for e in order_entries)

    # ---- Reserved balance is non-zero mid-flight -- a real reservation
    # was taken (not the vacuous case). ----
    assert await reserved_balance(session_factory, fixture.mandate.mandate_id) == (
        fixture.amount_minor
    )

    # ---- Property 2: reaches the required terminal state (settled). ----
    assert order.provider_order_id is not None
    payments = await provider.fetch_payments(order.provider_order_id)
    payment = payments[0]
    signature = provider.build_checkout_payload(order.provider_order_id, payment.id)
    result = await saga.complete_purchase(
        begin.saga_id, session_factory, provider, clock, breaker,
        provider_order_id=order.provider_order_id, provider_payment_id=payment.id,
        provider_signature=signature,
    )
    assert result.status == "COMPLETED"
    async with UnitOfWork(session_factory) as uow:
        final_order = await uow.orders.get(begin.order_id)
    assert final_order is not None
    assert final_order.status == "CAPTURED"

    # ---- Property 3: reserved ledger balance returns to exactly zero
    # (fully moved to settled). ----
    assert await reserved_balance(session_factory, fixture.mandate.mandate_id) == 0
    assert await settled_balance(session_factory, fixture.mandate.mandate_id) == (
        fixture.amount_minor
    )

    # ---- No duplicates: a second begin_purchase call for the exact same
    # request (mandate/intent/attempt_no) returns the same, already-
    # resolved saga -- never a second reservation or a second order. ----
    replay = await saga.begin_purchase(_req(fixture), session_factory, provider, clock, breaker)
    assert replay.order_id == begin.order_id
    assert replay.saga_id == begin.saga_id
    assert len(provider._orders) == 1
    assert await settled_balance(session_factory, fixture.mandate.mandate_id) == (
        fixture.amount_minor
    )
