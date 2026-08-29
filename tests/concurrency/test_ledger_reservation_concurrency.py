"""§28 P6 exit criteria / §12.1 WHY THIS WAY: "The corresponding test
spawns fifty concurrent attempts against a cap that admits three and
asserts exactly three succeed." Real Postgres, real `SELECT ... FOR
UPDATE` row-lock serialisation -- no mocking, per §18.1.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application import ledger_service
from actl.domain.ledger.model import net_balance
from actl.domain.mandate.state_machine import MandateStatus
from actl.infrastructure.db.uow import UnitOfWork
from actl.platform.clock import SystemClock
from actl.platform.ids import new_id
from tests.integration.db.conftest import make_locked_mandate

pytestmark = pytest.mark.asyncio(loop_scope="session")

N = 50
# make_locked_mandate()'s bounds fix max_total_minor at 900000; sized so
# exactly 3 of the 50 attempts fit (3 * 300000 == 900000, 4 * 300000 > it).
UNIT_MINOR = 300000
CAP_MINOR = 900000
EXPECTED_ADMITTED = 3


async def _attempt(session_factory: async_sessionmaker[AsyncSession], mandate_id: str) -> bool:
    clock = SystemClock()
    ref_id = new_id("rsv")
    async with UnitOfWork(session_factory) as uow:
        reservation = await ledger_service.reserve(
            uow,
            clock,
            mandate_id=mandate_id,
            amount_minor=UNIT_MINOR,
            max_total_minor=CAP_MINOR,
            ref_id=ref_id,
        )
        if reservation is None:
            return False
        await uow.commit()
    return True


async def _run_fifty_parallel_attempts_against_a_fresh_mandate(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    mandate = make_locked_mandate()
    async with UnitOfWork(session_factory) as uow:
        await uow.mandates.add(mandate, MandateStatus.LOCKED)
        await uow.commit()

    results = await asyncio.gather(
        *(_attempt(session_factory, mandate.mandate_id) for _ in range(N))
    )

    admitted = sum(1 for r in results if r)
    denied = sum(1 for r in results if not r)
    assert admitted == EXPECTED_ADMITTED, (
        f"expected exactly {EXPECTED_ADMITTED} admitted, got {admitted}"
    )
    assert denied == N - EXPECTED_ADMITTED

    # No over-reservation: the *net* reserved balance is exactly what the
    # cap allows, never more, regardless of how many of the 50 raced for it.
    async with UnitOfWork(session_factory) as uow:
        reserved_entries = await uow.ledger_entries.list_for_account(
            f"mandate:{mandate.mandate_id}:reserved"
        )
    held = net_balance([(e.direction, e.amount_minor) for e in reserved_entries])
    assert held == UNIT_MINOR * EXPECTED_ADMITTED

    # No duplicate ledger entries: exactly one debit row per admitted
    # reservation, each for exactly UNIT_MINOR -- never a partial or a
    # doubled-up insert from two racing transactions.
    assert len(reserved_entries) == EXPECTED_ADMITTED
    for entry in reserved_entries:
        assert entry.direction == "debit"
        assert entry.amount_minor == UNIT_MINOR
        assert entry.ref_type == "reservation"
    assert len({e.ref_id for e in reserved_entries}) == EXPECTED_ADMITTED  # distinct reservations


async def test_fifty_parallel_reservations_admit_exactly_the_cap(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _run_fifty_parallel_attempts_against_a_fresh_mandate(session_factory)


async def test_result_is_stable_across_repeated_runs(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """§28 P6 instruction 2: "prove the result remains correct across
    repeated runs." A fresh mandate each time -- reservations are scoped
    per-mandate by design, so this is 3 independent proofs of the same
    property, not one lucky race outcome."""
    for _ in range(3):
        await _run_fifty_parallel_attempts_against_a_fresh_mandate(session_factory)
