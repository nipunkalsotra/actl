"""§28 P6 exit criteria: test_gate_g4_no_overspend_under_concurrency (50
attempts, cap admits 3, exactly 3 allowed) -- through the *full* gate
(execute_money_action), not the ledger directly, so this also proves the
audit chain stays gapless/unforked under real concurrent G1-G7 traffic
against one mandate.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application.audit_service import verify_chain
from actl.application.gate import MoneyActionRequest, MoneyActionResult, execute_money_action
from actl.infrastructure.db.uow import UnitOfWork
from actl.infrastructure.providers.simulator.adapter import SimulatorAdapter
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import SystemClock
from actl.platform.ids import new_id
from tests.integration.gate.conftest import seed_valid_gate_fixture

pytestmark = pytest.mark.asyncio(loop_scope="session")

N = 50
UNIT_MINOR = 300000  # make_locked_mandate()'s 900000 cap admits exactly 3
EXPECTED_ADMITTED = 3


async def _attempt(
    session_factory: async_sessionmaker[AsyncSession],
    provider: SimulatorAdapter,
    clock: SystemClock,
    breaker: CircuitBreaker,
    req: MoneyActionRequest,
) -> MoneyActionResult:
    return await execute_money_action(req, session_factory, provider, clock, breaker)


async def test_gate_g4_no_overspend_under_concurrency(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    provider = SimulatorAdapter(clock=clock)
    breaker = CircuitBreaker(name="razorpay", clock=clock)
    fixture = await seed_valid_gate_fixture(session_factory, clock)

    # tests/integration/audit's tamper-detection test deliberately corrupts
    # a row elsewhere in this same session-scoped chain (§28 P3) -- verify
    # only *this test's own* segment, not the whole shared chain from seq=1.
    async with UnitOfWork(session_factory) as uow:
        await uow.audit_log.acquire_chain_lock("actl.audit_log")
        start_tail = await uow.audit_log.get_tail()
    start_seq = start_tail[0] if start_tail is not None else 0

    reqs = [
        MoneyActionRequest(
            trace_id=new_id("trc"),
            mandate_id=fixture.mandate.mandate_id,
            decision_id=fixture.decision_id,
            quote_id=fixture.quote_id,
            intent_hash=fixture.intent_hash,
            amount_minor=UNIT_MINOR,
            currency="INR",
            attempt_no=attempt_no,
        )
        for attempt_no in range(1, N + 1)
    ]

    results = await asyncio.gather(
        *(_attempt(session_factory, provider, clock, breaker, req) for req in reqs)
    )

    admitted = sum(1 for r in results if r.verdict == "ALLOW")
    assert admitted == EXPECTED_ADMITTED, f"expected exactly {EXPECTED_ADMITTED}, got {admitted}"

    async with UnitOfWork(session_factory) as uow:
        await uow.audit_log.acquire_chain_lock("actl.audit_log")
        tail = await uow.audit_log.get_tail()
    assert tail is not None

    async with UnitOfWork(session_factory) as uow:
        verification = await verify_chain(uow, start_seq + 1, tail[0])
    assert verification.ok, verification.break_
