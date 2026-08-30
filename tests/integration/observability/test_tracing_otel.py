"""§22 / §28 P10 exit criterion: a full transaction's OpenTelemetry spans
must share the *exact same* trace id as the audit chain's own `trace_id`
column for that transaction -- proven here against a real Postgres audit
log, not just the pure round-trip math in tests/unit/platform/test_tracing.py.

§11/§15's S1/S2 (`begin_purchase`) and S3/S4/S5 (`complete_purchase`) are
architecturally two separate transactions (the payer's checkout
confirmation is a later, out-of-band callback) -- each gets its own trace
id, verified independently below.
"""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application.gate import MoneyActionRequest
from actl.application.orchestrator import saga
from actl.infrastructure.db.uow import UnitOfWork
from actl.infrastructure.providers.simulator.adapter import SimulatorAdapter
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import SystemClock
from actl.platform.ids import new_id
from actl.platform.tracing import configure_tracing, otel_to_trace_id, trace_id_to_otel
from tests.integration.gate.conftest import seed_valid_gate_fixture

pytestmark = pytest.mark.asyncio(loop_scope="session")

_EXPECTED_BEGIN_PURCHASE_SPANS = {
    "saga.begin_purchase",
    "saga.S1_reserve",
    "saga.S2_order",
    "gate.execute_money_action",
    "gate.G1_mandate_validity",
    "gate.G2_decision_binding",
    "gate.G3_policy_verdict",
    "gate.G4_budget_reservation",
    "gate.G5_freshness",
    "gate.G6_G7_idempotency_audit_and_execute",
    "provider.create_order",
    "audit.append_entry",
}

_EXPECTED_COMPLETE_PURCHASE_SPANS = {
    "saga.complete_purchase",
    "saga.S3_authorize",
    "provider.fetch_payments",
    "saga.S4_capture",
    "provider.capture",
    "saga.S5_settle",
    "audit.append_entry",
}


async def test_begin_purchase_span_tree_matches_persisted_audit_trace_id(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    exporter = InMemorySpanExporter()
    configure_tracing(exporter)
    clock = SystemClock()
    provider = SimulatorAdapter(clock=clock)
    breaker = CircuitBreaker(name="razorpay", clock=clock)
    fixture = await seed_valid_gate_fixture(session_factory, clock)

    trace_id = new_id("trc")
    req = MoneyActionRequest(
        trace_id=trace_id,
        mandate_id=fixture.mandate.mandate_id,
        decision_id=fixture.decision_id,
        quote_id=fixture.quote_id,
        intent_hash=fixture.intent_hash,
        amount_minor=fixture.amount_minor,
        currency="INR",
        attempt_no=1,
    )
    result = await saga.begin_purchase(req, session_factory, provider, clock, breaker)
    assert result.status == "AWAITING_AUTHORIZATION"

    spans = exporter.get_finished_spans()
    span_names = {s.name for s in spans}
    assert span_names >= _EXPECTED_BEGIN_PURCHASE_SPANS, span_names - _EXPECTED_BEGIN_PURCHASE_SPANS

    # Every span this transaction produced carries the *exact* OTel trace
    # id that trace_id encodes -- not a merely-correlated, independently
    # minted value.
    expected_otel_id = trace_id_to_otel(trace_id)
    mismatched = [s.name for s in spans if s.context.trace_id != expected_otel_id]
    assert mismatched == [], f"spans with a different trace id: {mismatched}"

    # And the audit rows this same transaction actually wrote to Postgres
    # carry that identical trace_id string -- the other half of the
    # equality, proven against the real chain, not just in-memory spans.
    async with UnitOfWork(session_factory) as uow:
        entries = await uow.audit_log.get_by_trace_id(trace_id)
    assert len(entries) >= 2  # budget.reserved + payment.intent, at least
    actions = {e.action for e in entries}
    assert "budget.reserved" in actions
    assert "payment.intent" in actions
    for entry in entries:
        assert otel_to_trace_id(trace_id_to_otel(entry.trace_id)) == entry.trace_id


async def test_complete_purchase_span_tree_matches_persisted_audit_trace_id(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    exporter = InMemorySpanExporter()
    configure_tracing(exporter)
    clock = SystemClock()
    provider = SimulatorAdapter(clock=clock)
    breaker = CircuitBreaker(name="razorpay", clock=clock)
    fixture = await seed_valid_gate_fixture(session_factory, clock)

    begin_req = MoneyActionRequest(
        trace_id=new_id("trc"),
        mandate_id=fixture.mandate.mandate_id,
        decision_id=fixture.decision_id,
        quote_id=fixture.quote_id,
        intent_hash=fixture.intent_hash,
        amount_minor=fixture.amount_minor,
        currency="INR",
        attempt_no=1,
    )
    begin = await saga.begin_purchase(begin_req, session_factory, provider, clock, breaker)
    assert begin.status == "AWAITING_AUTHORIZATION"
    assert begin.order_id is not None

    async with UnitOfWork(session_factory) as uow:
        order = await uow.orders.get(begin.order_id)
    assert order is not None
    assert order.provider_order_id is not None
    payments = await provider.fetch_payments(order.provider_order_id)
    payment = payments[0]
    signature = provider.build_checkout_payload(order.provider_order_id, payment.id)

    exporter.clear()  # only interested in complete_purchase's own span tree now
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

    spans = exporter.get_finished_spans()
    span_names = {s.name for s in spans}
    assert span_names >= _EXPECTED_COMPLETE_PURCHASE_SPANS, (
        span_names - _EXPECTED_COMPLETE_PURCHASE_SPANS
    )

    root_trace_id = spans[0].context.trace_id
    mismatched = [s.name for s in spans if s.context.trace_id != root_trace_id]
    assert mismatched == [], f"spans with a different trace id: {mismatched}"

    # complete_purchase mints its own trace_id internally (S3/S4/S5 is a
    # separate transaction from S1/S2, per saga.py's own docstring) -- the
    # audit trail's settlement.closed entry must carry the *same* value
    # this span tree's OTel trace id decodes back to.
    derived_trace_id = otel_to_trace_id(root_trace_id)
    async with UnitOfWork(session_factory) as uow:
        entries = await uow.audit_log.get_by_trace_id(derived_trace_id)
    actions = {e.action for e in entries}
    assert "settlement.closed" in actions
