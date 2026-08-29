"""§28 P6 exit criteria: saga forward path S1-S5, each compensation
failure point (C1-C5) in strict reverse, replay/restart-safety, and the
cancellation/timeout/stale-quote/declined-payment paths. Real Postgres;
the SimulatorAdapter drives every payment outcome deterministically --
never a real Razorpay call in the normal suite (§28 P6 instruction 4).
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application import ledger_service
from actl.application.gate import MoneyActionRequest
from actl.application.orchestrator import saga
from actl.application.payment_service import compute_idempotency_key
from actl.domain.ledger.model import ReservationState, account, net_balance
from actl.domain.mandate.state_machine import MandateStatus
from actl.domain.policy.reason_codes import ReasonCode
from actl.infrastructure.db.repositories.sagas import SagaRecord
from actl.infrastructure.db.uow import UnitOfWork
from actl.infrastructure.providers.simulator.adapter import Scenario, SimulatorAdapter
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import SystemClock
from actl.platform.ids import new_id
from tests.integration.gate.conftest import GateFixture, seed_valid_gate_fixture

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _req(fixture: GateFixture, *, attempt_no: int = 1) -> MoneyActionRequest:
    return MoneyActionRequest(
        trace_id=new_id("trc"),
        mandate_id=fixture.mandate.mandate_id,
        decision_id=fixture.decision_id,
        quote_id=fixture.quote_id,
        intent_hash=fixture.intent_hash,
        amount_minor=fixture.amount_minor,
        currency="INR",
        attempt_no=attempt_no,
    )


async def _reserved_balance(
    session_factory: async_sessionmaker[AsyncSession], mandate_id: str
) -> int:
    async with UnitOfWork(session_factory) as uow:
        entries = await uow.ledger_entries.list_for_account(account(mandate_id, "reserved"))
    return net_balance([(e.direction, e.amount_minor) for e in entries])


async def _settled_balance(
    session_factory: async_sessionmaker[AsyncSession], mandate_id: str
) -> int:
    async with UnitOfWork(session_factory) as uow:
        entries = await uow.ledger_entries.list_for_account(account(mandate_id, "settled"))
    return net_balance([(e.direction, e.amount_minor) for e in entries])


async def _mandate_status(
    session_factory: async_sessionmaker[AsyncSession], mandate_id: str
) -> MandateStatus:
    async with UnitOfWork(session_factory) as uow:
        current = await uow.mandates.get(mandate_id)
    assert current is not None
    return current[1]


# ---------------------------------------------------------------------------
# Forward path: S1-S5 all succeed
# ---------------------------------------------------------------------------


async def test_saga_happy_path_settles(session_factory: async_sessionmaker[AsyncSession]) -> None:
    clock = SystemClock()
    provider = SimulatorAdapter(clock=clock)
    breaker = CircuitBreaker(name="razorpay", clock=clock)
    fixture = await seed_valid_gate_fixture(session_factory, clock)

    begin = await saga.begin_purchase(_req(fixture), session_factory, provider, clock, breaker)
    assert begin.status == "AWAITING_AUTHORIZATION"
    assert begin.order_id is not None

    async with UnitOfWork(session_factory) as uow:
        order = await uow.orders.get(begin.order_id)
    assert order is not None
    assert order.provider_order_id is not None
    payments = await provider.fetch_payments(order.provider_order_id)
    payment = payments[0]
    signature = provider.build_checkout_payload(order.provider_order_id, payment.id)

    result = await saga.complete_purchase(
        begin.saga_id,
        session_factory,
        provider,
        clock,
        breaker,
        provider_order_id=order.provider_order_id,
        provider_payment_id=payment.id,
        provider_signature=signature,
    )

    assert result.status == "COMPLETED"
    assert result.step == "S5_SETTLE"
    assert (
        await _mandate_status(session_factory, fixture.mandate.mandate_id) == MandateStatus.SETTLED
    )
    assert await _reserved_balance(session_factory, fixture.mandate.mandate_id) == 0
    assert (
        await _settled_balance(session_factory, fixture.mandate.mandate_id) == fixture.amount_minor
    )

    async with UnitOfWork(session_factory) as uow:
        final_order = await uow.orders.get(begin.order_id)
    assert final_order is not None
    assert final_order.status == "CAPTURED"


# ---------------------------------------------------------------------------
# S2 (order creation) fails -- the gate's own C1 self-compensation
# ---------------------------------------------------------------------------


async def test_saga_transient_order_creation_failure_compensates(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    provider = SimulatorAdapter(
        clock=clock, scenario=Scenario.TRANSIENT_FAILURE, fail_before_success=999
    )
    breaker = CircuitBreaker(name="razorpay", clock=clock, failure_threshold=999)
    fixture = await seed_valid_gate_fixture(session_factory, clock)

    begin = await saga.begin_purchase(_req(fixture), session_factory, provider, clock, breaker)

    assert begin.status == "FAILED"
    assert begin.reason_code == ReasonCode.PROVIDER_TRANSIENT
    assert await _reserved_balance(session_factory, fixture.mandate.mandate_id) == 0
    assert (
        await _mandate_status(session_factory, fixture.mandate.mandate_id)
        == MandateStatus.COMPENSATED
    )


# ---------------------------------------------------------------------------
# S3 AUTHORIZE declines -- C2 VOID then C1 RELEASE, strict reverse
# ---------------------------------------------------------------------------


async def test_saga_declined_authorization_compensates_c2_then_c1(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    provider = SimulatorAdapter(clock=clock, scenario=Scenario.DECLINE)
    breaker = CircuitBreaker(name="razorpay", clock=clock)
    fixture = await seed_valid_gate_fixture(session_factory, clock)

    begin = await saga.begin_purchase(_req(fixture), session_factory, provider, clock, breaker)
    assert begin.status == "AWAITING_AUTHORIZATION"
    assert begin.order_id is not None

    async with UnitOfWork(session_factory) as uow:
        order = await uow.orders.get(begin.order_id)
    assert order is not None
    assert order.provider_order_id is not None
    payments = await provider.fetch_payments(order.provider_order_id)
    declined_payment = payments[0]
    assert declined_payment.status == "failed"

    result = await saga.complete_purchase(
        begin.saga_id,
        session_factory,
        provider,
        clock,
        breaker,
        provider_order_id=order.provider_order_id,
        provider_payment_id=declined_payment.id,
        provider_signature="irrelevant-a-decline-never-produces-one",
    )

    assert result.status == "COMPENSATED"
    assert result.step == "C2_VOID"
    assert await _reserved_balance(session_factory, fixture.mandate.mandate_id) == 0
    assert (
        await _mandate_status(session_factory, fixture.mandate.mandate_id)
        == MandateStatus.COMPENSATED
    )

    async with UnitOfWork(session_factory) as uow:
        entries = await uow.ledger_entries.list_for_ref_id(begin.saga_id)
    from actl.application.ledger_service import _state_of

    assert _state_of(entries) == ReservationState.RELEASED

    async with UnitOfWork(session_factory) as uow:
        final_order = await uow.orders.get(begin.order_id)
    assert final_order is not None
    assert final_order.status == "FAILED"


# ---------------------------------------------------------------------------
# S4 CAPTURE: a tampered checkout signature -- C2 VOID then C1 RELEASE
# ---------------------------------------------------------------------------


async def test_saga_tampered_signature_compensates(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    provider = SimulatorAdapter(clock=clock)
    breaker = CircuitBreaker(name="razorpay", clock=clock)
    fixture = await seed_valid_gate_fixture(session_factory, clock)

    begin = await saga.begin_purchase(_req(fixture), session_factory, provider, clock, breaker)
    assert begin.order_id is not None
    async with UnitOfWork(session_factory) as uow:
        order = await uow.orders.get(begin.order_id)
    assert order is not None
    assert order.provider_order_id is not None
    payments = await provider.fetch_payments(order.provider_order_id)
    payment = payments[0]
    valid_signature = provider.build_checkout_payload(order.provider_order_id, payment.id)
    tampered_signature = valid_signature[:-1] + ("0" if valid_signature[-1] != "0" else "1")

    result = await saga.complete_purchase(
        begin.saga_id,
        session_factory,
        provider,
        clock,
        breaker,
        provider_order_id=order.provider_order_id,
        provider_payment_id=payment.id,
        provider_signature=tampered_signature,
    )

    assert result.status == "COMPENSATED"
    assert result.step == "C2_VOID"
    assert await _reserved_balance(session_factory, fixture.mandate.mandate_id) == 0

    async with UnitOfWork(session_factory) as uow:
        final_order = await uow.orders.get(begin.order_id)
    assert final_order is not None
    assert final_order.status == "FAILED"  # never captured -- signature never verified


# ---------------------------------------------------------------------------
# S5 SETTLE: ledger can't record a capture that already happened at the
# provider -- C4 REFUND then C5 REVERSE, strict reverse
# ---------------------------------------------------------------------------


async def test_saga_c4_refund_and_c5_reverse_when_ledger_capture_fails(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    provider = SimulatorAdapter(clock=clock)
    breaker = CircuitBreaker(name="razorpay", clock=clock)
    fixture = await seed_valid_gate_fixture(session_factory, clock)

    begin = await saga.begin_purchase(_req(fixture), session_factory, provider, clock, breaker)
    assert begin.order_id is not None
    async with UnitOfWork(session_factory) as uow:
        order = await uow.orders.get(begin.order_id)
    assert order is not None
    assert order.provider_order_id is not None
    payments = await provider.fetch_payments(order.provider_order_id)
    payment = payments[0]
    signature = provider.build_checkout_payload(order.provider_order_id, payment.id)

    # Fault injection: the reservation is released out-of-band (as a
    # sweeper would do) *before* complete_purchase runs S5 -- simulating
    # "the provider-side capture is about to succeed, but the local
    # ledger has nothing HELD left to record it against."
    async with UnitOfWork(session_factory) as uow:
        released = await ledger_service.release(
            uow,
            clock,
            mandate_id=fixture.mandate.mandate_id,
            amount_minor=fixture.amount_minor,
            ref_id=begin.saga_id,
        )
        await uow.commit()
    assert released is True

    result = await saga.complete_purchase(
        begin.saga_id,
        session_factory,
        provider,
        clock,
        breaker,
        provider_order_id=order.provider_order_id,
        provider_payment_id=payment.id,
        provider_signature=signature,
    )

    assert result.status == "COMPENSATED"
    assert result.step == "C5_REVERSE"

    async with UnitOfWork(session_factory) as uow:
        final_order = await uow.orders.get(begin.order_id)
    assert final_order is not None
    assert final_order.status == "FAILED"  # refunded, never left CAPTURED
    assert await _settled_balance(session_factory, fixture.mandate.mandate_id) == 0


# ---------------------------------------------------------------------------
# Cancellation: mandate revoked between begin and complete
# ---------------------------------------------------------------------------


async def test_saga_honors_revocation_between_begin_and_complete(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    provider = SimulatorAdapter(clock=clock)
    breaker = CircuitBreaker(name="razorpay", clock=clock)
    fixture = await seed_valid_gate_fixture(session_factory, clock)

    begin = await saga.begin_purchase(_req(fixture), session_factory, provider, clock, breaker)
    assert begin.status == "AWAITING_AUTHORIZATION"
    assert begin.order_id is not None
    async with UnitOfWork(session_factory) as uow:
        order = await uow.orders.get(begin.order_id)
    assert order is not None
    assert order.provider_order_id is not None
    payments = await provider.fetch_payments(order.provider_order_id)
    payment = payments[0]
    signature = provider.build_checkout_payload(order.provider_order_id, payment.id)

    # The kill-switch: revoked mid-flight, before checkout completes.
    async with UnitOfWork(session_factory) as uow:
        await uow.mandates.update_status(fixture.mandate.mandate_id, MandateStatus.REVOKED)
        await uow.commit()

    result = await saga.complete_purchase(
        begin.saga_id,
        session_factory,
        provider,
        clock,
        breaker,
        provider_order_id=order.provider_order_id,
        provider_payment_id=payment.id,
        provider_signature=signature,
    )

    assert result.status == "COMPENSATED"
    assert result.step == "C2_VOID"
    assert await _reserved_balance(session_factory, fixture.mandate.mandate_id) == 0
    # revocation is monotonic (I-M3) -- compensating never overwrites it
    assert (
        await _mandate_status(session_factory, fixture.mandate.mandate_id) == MandateStatus.REVOKED
    )


# ---------------------------------------------------------------------------
# Idempotent replay / restart-safety
# ---------------------------------------------------------------------------


async def test_saga_begin_purchase_replay_is_a_pure_read(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    provider = SimulatorAdapter(clock=clock)
    breaker = CircuitBreaker(name="razorpay", clock=clock)
    fixture = await seed_valid_gate_fixture(session_factory, clock)
    req = _req(fixture)

    first = await saga.begin_purchase(req, session_factory, provider, clock, breaker)
    second = await saga.begin_purchase(req, session_factory, provider, clock, breaker)

    assert second.saga_id == first.saga_id
    assert second.order_id == first.order_id
    assert second.status == first.status
    # exactly one reservation and one order, never a second from the replay
    assert (
        await _reserved_balance(session_factory, fixture.mandate.mandate_id) == fixture.amount_minor
    )


async def test_saga_begin_purchase_resumes_a_crashed_running_saga(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A saga row persisted as RUNNING (§15 "saga state ... committed
    before the side effect") with no further progress models a crash
    between that write and the gate call returning. A resumed
    begin_purchase call must make real progress, not report stuck-forever
    RUNNING."""
    clock = SystemClock()
    provider = SimulatorAdapter(clock=clock)
    breaker = CircuitBreaker(name="razorpay", clock=clock)
    fixture = await seed_valid_gate_fixture(session_factory, clock)
    req = _req(fixture)
    key = compute_idempotency_key(fixture.mandate.mandate_id, fixture.intent_hash, 1)

    async with UnitOfWork(session_factory) as uow:
        await uow.sagas.add(
            SagaRecord(
                id=key,
                mandate_id=fixture.mandate.mandate_id,
                decision_id=fixture.decision_id,
                quote_id=fixture.quote_id,
                amount_minor=fixture.amount_minor,
                currency="INR",
                step="S1_RESERVE",
                status="RUNNING",
            ),
            created_at=clock.now(),
        )
        await uow.commit()

    result = await saga.begin_purchase(req, session_factory, provider, clock, breaker)

    assert result.saga_id == key
    assert result.status == "AWAITING_AUTHORIZATION"
    assert result.order_id is not None
    assert (
        await _reserved_balance(session_factory, fixture.mandate.mandate_id) == fixture.amount_minor
    )


async def test_saga_complete_purchase_replay_is_a_pure_read(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    provider = SimulatorAdapter(clock=clock)
    breaker = CircuitBreaker(name="razorpay", clock=clock)
    fixture = await seed_valid_gate_fixture(session_factory, clock)

    begin = await saga.begin_purchase(_req(fixture), session_factory, provider, clock, breaker)
    assert begin.order_id is not None
    async with UnitOfWork(session_factory) as uow:
        order = await uow.orders.get(begin.order_id)
    assert order is not None
    assert order.provider_order_id is not None
    payments = await provider.fetch_payments(order.provider_order_id)
    payment = payments[0]
    signature = provider.build_checkout_payload(order.provider_order_id, payment.id)

    first = await saga.complete_purchase(
        begin.saga_id,
        session_factory,
        provider,
        clock,
        breaker,
        provider_order_id=order.provider_order_id,
        provider_payment_id=payment.id,
        provider_signature=signature,
    )
    second = await saga.complete_purchase(
        begin.saga_id,
        session_factory,
        provider,
        clock,
        breaker,
        provider_order_id=order.provider_order_id,
        provider_payment_id=payment.id,
        provider_signature=signature,
    )

    assert first.status == "COMPLETED"
    assert second.status == "COMPLETED"
    # settled exactly once -- the replay never re-captures or re-settles
    assert (
        await _settled_balance(session_factory, fixture.mandate.mandate_id) == fixture.amount_minor
    )
