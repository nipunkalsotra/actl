"""§20 F2: "Payment declined by the provider -- Terminal provider status
-- Compensate in reverse, release reservation, no blind retry." Terminal
class.

The decline is the `SimulatorAdapter`'s own `Scenario.DECLINE` -- a real,
deterministic simulated provider response (real HMAC-signed checkout
payload machinery, just carrying a "failed" payment status), never a
patched-in fault -- matching `tests/integration/gate/test_saga.py::
test_saga_declined_authorization_compensates_c2_then_c1`'s own precedent
(§28 P6), which this chaos-layer test mirrors and extends with the
explicit no-duplicate-compensation proof §28 P9 instruction 2 requires.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application.gate import MoneyActionRequest
from actl.application.ledger_service import _state_of
from actl.application.orchestrator import saga
from actl.domain.ledger.model import ReservationState
from actl.domain.mandate.state_machine import MandateStatus
from actl.infrastructure.db.uow import UnitOfWork
from actl.infrastructure.providers.simulator.adapter import Scenario, SimulatorAdapter
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import SystemClock
from actl.platform.ids import new_id
from tests.chaos._helpers import reserved_balance
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


async def _mandate_status(
    session_factory: async_sessionmaker[AsyncSession], mandate_id: str
) -> MandateStatus:
    async with UnitOfWork(session_factory) as uow:
        got = await uow.mandates.get(mandate_id)
    assert got is not None
    return got[1]


async def test_declined_payment_compensates_and_releases_reservation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    provider = SimulatorAdapter(clock=clock, scenario=Scenario.DECLINE)
    breaker = CircuitBreaker(name="f2-chaos", clock=clock)
    fixture = await seed_valid_gate_fixture(session_factory, clock)

    begin = await saga.begin_purchase(_req(fixture), session_factory, provider, clock, breaker)
    assert begin.status == "AWAITING_AUTHORIZATION"
    assert begin.order_id is not None

    async with UnitOfWork(session_factory) as uow:
        order = await uow.orders.get(begin.order_id)
    assert order is not None and order.provider_order_id is not None
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

    # ---- Property 1: typed status, reason, and audit evidence. ----
    assert result.status == "COMPENSATED"
    assert result.step == "C2_VOID"
    async with UnitOfWork(session_factory) as uow:
        seq_range = await uow.audit_log.get_seq_range_for_order(begin.order_id)
        assert seq_range is not None
        order_entries = await uow.audit_log.list_range(*seq_range)
    comp_entries = [e for e in order_entries if e.action == "compensation.applied"]
    assert len(comp_entries) >= 1
    assert comp_entries[0].payload["reason"] in ("payment_declined", "order_creation_failed")

    # ---- Property 2: reaches the required terminal state. ----
    assert await _mandate_status(session_factory, fixture.mandate.mandate_id) == (
        MandateStatus.COMPENSATED
    )
    async with UnitOfWork(session_factory) as uow:
        final_order = await uow.orders.get(begin.order_id)
    assert final_order is not None
    assert final_order.status == "FAILED"

    # ---- Property 3: reserved ledger balance returns to exactly zero. ----
    assert await reserved_balance(session_factory, fixture.mandate.mandate_id) == 0
    async with UnitOfWork(session_factory) as uow:
        entries = await uow.ledger_entries.list_for_ref_id(begin.saga_id)
    assert _state_of(entries) == ReservationState.RELEASED

    # ---- No duplicates: replaying complete_purchase for the same saga
    # must not compensate a second time, capture, or add ledger entries. ----
    entries_before = len(entries)
    replay = await saga.complete_purchase(
        begin.saga_id,
        session_factory,
        provider,
        clock,
        breaker,
        provider_order_id=order.provider_order_id,
        provider_payment_id=declined_payment.id,
        provider_signature="irrelevant-a-decline-never-produces-one",
    )
    assert replay.status == "COMPENSATED"
    async with UnitOfWork(session_factory) as uow:
        entries_after = await uow.ledger_entries.list_for_ref_id(begin.saga_id)
    assert len(entries_after) == entries_before
    assert await reserved_balance(session_factory, fixture.mandate.mandate_id) == 0
