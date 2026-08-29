"""§20 F3: "Webhook never arrives -- Reconciler finds a non-terminal order
past threshold -- Poll provider, settle from the polled state." Transient
class. Migrated and extended from the earlier combined
tests/chaos/test_f3_f4_f5.py (§28 P5 exit criteria's own
test_missing_webhook_recovered_by_reconciler) with the explicit reserved-
balance-zero and no-duplicate-settlement proofs §28 P9 instruction 2 adds.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application.payment_service import create_provider_order, reconcile_non_terminal_orders
from actl.infrastructure.db.uow import UnitOfWork
from actl.infrastructure.providers.simulator.adapter import SimulatorAdapter
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import FrozenClock, SystemClock
from actl.platform.ids import new_id
from tests.chaos._helpers import reserved_balance
from tests.integration.payments.conftest import seed_purchase_fixture

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_missing_webhook_recovered_by_reconciler(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """No webhook is ever delivered for this order -- capture() is called
    directly against the provider (as auto-capture or an out-of-band
    Checkout success would leave it), simulating exactly the webhook that
    never arrived."""
    clock = FrozenClock(at=SystemClock().now())
    fixture = await seed_purchase_fixture(session_factory, clock)
    provider = SimulatorAdapter(clock=clock)
    breaker = CircuitBreaker(name="f3-chaos", clock=clock)
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

    # ---- Property 1: typed status, reason, and audit evidence. ----
    matching = [o for o in outcomes if o.order_id == order_id]
    assert len(matching) == 1
    assert matching[0].action == "captured"
    async with UnitOfWork(session_factory) as uow:
        seq_range = await uow.audit_log.get_seq_range_for_order(order_id)
        assert seq_range is not None
        order_entries = await uow.audit_log.list_range(*seq_range)
    reconciled_entries = [
        e
        for e in order_entries
        if e.action == "payment.result" and e.payload.get("source") == "reconciler"
    ]
    assert len(reconciled_entries) == 1
    assert reconciled_entries[0].payload["status"] == "captured"

    # ---- Property 2: reaches the required terminal state. ----
    async with UnitOfWork(session_factory) as uow:
        after = await uow.orders.get(order_id)
    assert after is not None
    assert after.status == "CAPTURED"
    assert after.provider_payment_id == payments[0].id

    # ---- Property 3: reserved ledger balance returns to exactly zero.
    # `create_provider_order` is called directly here (the same P5-era
    # call this test's own precedent uses), below G4's reservation layer
    # -- no reservation was ever taken for this order, so this holds
    # vacuously rather than via a release; `tests/chaos/test_f5.py` (the
    # sibling failure mode driven through the full gate) is where a real
    # reservation is taken and this property is non-trivial. ----
    assert await reserved_balance(session_factory, fixture.mandate_id) == 0

    # ---- No duplicates: reconciling again is a no-op, never a second
    # settlement audit entry or a changed order state. ----
    async with UnitOfWork(session_factory) as uow:
        second_pass = await reconcile_non_terminal_orders(
            uow, provider, clock, breaker, reconcile_after_s=45
        )
    assert not [o for o in second_pass if o.order_id == order_id]
    async with UnitOfWork(session_factory) as uow:
        seq_range_after = await uow.audit_log.get_seq_range_for_order(order_id)
        assert seq_range_after is not None
        order_entries_after = await uow.audit_log.list_range(*seq_range_after)
    reconciled_after = [
        e
        for e in order_entries_after
        if e.action == "payment.result" and e.payload.get("source") == "reconciler"
    ]
    assert len(reconciled_after) == 1  # still exactly one, not a second
