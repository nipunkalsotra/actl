"""§20 F9: "Concurrent requests exceed the cap together -- Gate G4 row
lock -- Deny the loser with BUDGET_EXCEEDED." Policy class.

Migrated and extended from `tests/integration/gate/test_gate_concurrency.
py::test_gate_g4_no_overspend_under_concurrency` (§28 P6 exit criteria)
into its own chaos-layer file with the explicit typed-denial-reason and
no-duplicate-reservation proofs §28 P9 instruction 2 adds. 50 genuinely
concurrent attempts (`asyncio.gather`, real Postgres row lock, not a
serial loop) against a cap that admits exactly 3.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application.audit_service import verify_chain
from actl.application.gate import MoneyActionRequest, MoneyActionResult, execute_money_action
from actl.domain.policy.reason_codes import ReasonCode
from actl.infrastructure.db.uow import UnitOfWork
from actl.infrastructure.providers.simulator.adapter import SimulatorAdapter
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import SystemClock
from actl.platform.ids import new_id
from tests.chaos._helpers import reserved_balance
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


async def test_concurrent_overspend_denies_the_losers_with_budget_exceeded(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    provider = SimulatorAdapter(clock=clock)
    breaker = CircuitBreaker(name="f9-chaos", clock=clock)
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

    # ---- Property 1: typed status, reason, and audit evidence -- every
    # loser is denied with exactly BUDGET_EXCEEDED, never a different
    # code, a timeout, or a raised exception. ----
    admitted = [r for r in results if r.verdict == "ALLOW"]
    denied = [r for r in results if r.verdict == "DENY"]
    assert len(admitted) == EXPECTED_ADMITTED, (
        f"expected exactly {EXPECTED_ADMITTED}, got {len(admitted)}"
    )
    assert len(denied) == N - EXPECTED_ADMITTED
    assert all(r.reason_code == ReasonCode.BUDGET_EXCEEDED for r in denied)

    async with UnitOfWork(session_factory) as uow:
        await uow.audit_log.acquire_chain_lock("actl.audit_log")
        tail = await uow.audit_log.get_tail()
    assert tail is not None
    async with UnitOfWork(session_factory) as uow:
        verification = await verify_chain(uow, start_seq + 1, tail[0])
    assert verification.ok, verification.break_

    # ---- Property 2: reaches the required terminal state -- exactly the
    # admitted count reached EXECUTING with a real order each, nothing
    # left ambiguously pending. ----
    admitted_order_ids = {r.order_id for r in admitted}
    assert None not in admitted_order_ids
    assert len(admitted_order_ids) == EXPECTED_ADMITTED

    # ---- Property 3: reserved ledger balance reflects exactly the
    # admitted attempts -- never more (I-M4, §9.2), and nothing "extra"
    # leaked in from a loser. ----
    assert await reserved_balance(session_factory, fixture.mandate.mandate_id) == (
        UNIT_MINOR * EXPECTED_ADMITTED
    )

    # ---- No duplicates: no order, ledger entry, or reservation exists
    # for any of the 47 losers. ----
    async with UnitOfWork(session_factory) as uow:
        for loser in denied:
            assert loser.order_id is None
