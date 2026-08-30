"""§22 / §28 P10 instruction 2: the Prometheus counters/gauges actually
move when the real money path runs -- not just when a test calls
`.inc()` directly (tests/unit/platform/test_metrics.py covers that).

Metric objects are process-global (`platform.metrics.REGISTRY`), so every
assertion here is a *delta* across one action, never an absolute value --
other tests in the same pytest process touch the same counters.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application.gate import MoneyActionRequest, execute_money_action
from actl.domain.policy.reason_codes import ReasonCode
from actl.infrastructure.providers.simulator.adapter import SimulatorAdapter
from actl.platform import metrics
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import SystemClock
from actl.platform.ids import new_id
from tests.integration.gate.conftest import seed_valid_gate_fixture

pytestmark = pytest.mark.asyncio(loop_scope="session")

_ALL_GATES = ("G1", "G2", "G3", "G4", "G5", "G6_G7")


async def test_gate_denial_increments_gate_denials_total_by_gate(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    provider = SimulatorAdapter(clock=clock)
    breaker = CircuitBreaker(name="razorpay", clock=clock)
    fixture = await seed_valid_gate_fixture(session_factory, clock)

    before = metrics.gate_denials_total.labels(gate="G4")._value.get()

    # A request over the mandate's own max_total_minor bound always fails
    # G4 (BUDGET_EXCEEDED), regardless of everything else about the fixture.
    req = MoneyActionRequest(
        trace_id=new_id("trc"),
        mandate_id=fixture.mandate.mandate_id,
        decision_id=fixture.decision_id,
        quote_id=fixture.quote_id,
        intent_hash=fixture.intent_hash,
        amount_minor=fixture.mandate.bounds.max_total_minor + 1,
        currency="INR",
        attempt_no=1,
    )
    result = await execute_money_action(req, session_factory, provider, clock, breaker)
    assert result.verdict == "DENY"
    assert result.reason_code == ReasonCode.BUDGET_EXCEEDED

    after = metrics.gate_denials_total.labels(gate="G4")._value.get()
    assert after == before + 1


async def test_gate_allow_increments_no_gate_denial(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A metric that never fires for the happy path is just as important
    to prove as one that does -- ALLOW must not silently count as some
    gate's denial."""
    clock = SystemClock()
    provider = SimulatorAdapter(clock=clock)
    breaker = CircuitBreaker(name="razorpay", clock=clock)
    fixture = await seed_valid_gate_fixture(session_factory, clock)

    before = {g: metrics.gate_denials_total.labels(gate=g)._value.get() for g in _ALL_GATES}

    req = MoneyActionRequest(
        trace_id=new_id("trc"),
        mandate_id=fixture.mandate.mandate_id,
        decision_id=fixture.decision_id,
        quote_id=fixture.quote_id,
        intent_hash=fixture.intent_hash,
        amount_minor=fixture.amount_minor,
        currency="INR",
        attempt_no=1,
    )
    result = await execute_money_action(req, session_factory, provider, clock, breaker)
    assert result.verdict == "ALLOW"

    after = {g: metrics.gate_denials_total.labels(gate=g)._value.get() for g in _ALL_GATES}
    assert after == before
