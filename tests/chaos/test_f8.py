"""§20 F8: "Mandate expires mid-flight -- Gate G1 on the next money
action -- Halt, compensate, ask the human for a fresh mandate." Policy
class.

"Mid-flight" is modeled precisely: a first purchase attempt succeeds
while the mandate is still valid (a real reservation is taken, mandate
LOCKED -> EXECUTING), the injected `FrozenClock` then advances past the
mandate's own `expires_at`, and a *second* money action against the same
mandate -- exactly "the next money action" §20 names -- is what G1
actually catches. The orphaned reservation from the first attempt is
released by `ledger_service.sweep` (§12.2), the existing TTL-based
compensation mechanism, once it is older than `reservation_ttl_s` --
"ask the human for a fresh mandate" is the operational fact that nothing
in this codebase automatically issues a replacement mandate.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application import ledger_service
from actl.application.gate import MoneyActionRequest, execute_money_action
from actl.domain.policy.reason_codes import ReasonCode
from actl.infrastructure.db.uow import UnitOfWork
from actl.infrastructure.providers.simulator.adapter import SimulatorAdapter
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import FrozenClock
from actl.platform.ids import new_id
from tests.chaos._helpers import build_mandate, reserved_balance
from tests.integration.gate.conftest import (
    GateFixture,
    seed_decision,
    seed_mandate,
    seed_quote,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _req(fixture: GateFixture, *, attempt_no: int) -> MoneyActionRequest:
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


async def test_mandate_expiring_mid_flight_halts_the_next_action_and_sweeps_clean(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    clock = FrozenClock(at=now)
    provider = SimulatorAdapter(clock=clock)
    breaker = CircuitBreaker(name="f8-chaos", clock=clock)

    # Expires soon -- signed *after* expires_at is set (build_mandate signs
    # the final object), unlike mutating an already-signed Mandate, which
    # would invalidate spec_hash and misleadingly deny MANDATE_TAMPERED
    # instead of the MANDATE_EXPIRED this test actually exercises.
    mandate = build_mandate(expires_at=(now + timedelta(hours=1)).isoformat())
    await seed_mandate(session_factory, mandate)
    intent_hash_1 = new_id("intent")
    decision_id_1 = await seed_decision(
        session_factory, clock, mandate=mandate, intent_hash=intent_hash_1, verdict="ALLOW"
    )
    quote_id_1 = await seed_quote(session_factory, clock, mandate_id=mandate.mandate_id)
    fixture_1 = GateFixture(
        mandate=mandate,
        decision_id=decision_id_1,
        quote_id=quote_id_1,
        intent_hash=intent_hash_1,
        amount_minor=280000 * 3,
    )

    # ---- First attempt: mandate is still valid, a real reservation is
    # taken, mandate -> EXECUTING. ----
    first = await execute_money_action(
        _req(fixture_1, attempt_no=1), session_factory, provider, clock, breaker
    )
    assert first.verdict == "ALLOW"
    assert await reserved_balance(session_factory, mandate.mandate_id) == fixture_1.amount_minor

    # ---- Fault injection: advance the injected clock past expires_at --
    # "mid-flight," the reservation from the first attempt is still open. ----
    clock.advance(timedelta(hours=2))

    intent_hash_2 = new_id("intent")
    decision_id_2 = await seed_decision(
        session_factory, clock, mandate=mandate, intent_hash=intent_hash_2, verdict="ALLOW"
    )
    quote_id_2 = await seed_quote(session_factory, clock, mandate_id=mandate.mandate_id)
    fixture_2 = GateFixture(
        mandate=mandate,
        decision_id=decision_id_2,
        quote_id=quote_id_2,
        intent_hash=intent_hash_2,
        amount_minor=280000 * 3,
    )

    # ---- Property 1: typed status, reason, and audit evidence -- "the
    # next money action" is denied at G1, exactly as §20 names. ----
    second = await execute_money_action(
        _req(fixture_2, attempt_no=2), session_factory, provider, clock, breaker
    )
    assert second.verdict == "DENY"
    assert second.reason_code == ReasonCode.MANDATE_EXPIRED

    # ---- Property 2: reaches the required terminal state -- the first
    # attempt's reservation is swept (released) once past its own TTL;
    # nothing auto-issues a fresh mandate (an operator/human must). ----
    async with UnitOfWork(session_factory) as uow:
        swept = await ledger_service.sweep(uow, clock, reservation_ttl_s=60)
        await uow.commit()
    assert len(swept) >= 1

    # ---- Property 3: reserved ledger balance returns to exactly zero. ----
    assert await reserved_balance(session_factory, mandate.mandate_id) == 0

    # ---- No duplicates: sweeping again is a no-op, never a second
    # release for the same reservation. ----
    async with UnitOfWork(session_factory) as uow:
        swept_again = await ledger_service.sweep(uow, clock, reservation_ttl_s=60)
        await uow.commit()
    assert swept_again == []
    assert await reserved_balance(session_factory, mandate.mandate_id) == 0
