"""§28 P8 instruction 9 / exit criteria: `run_growth_simulation` (the
engine behind `actl growth --seed demo --sessions N`) through the real
gate/saga/ledger with a real Postgres container and the `SimulatorAdapter`
-- never Razorpay, never Groq.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application.growth.events import ARM_BASELINE, ARM_UPSELL
from actl.application.growth.simulation import run_growth_simulation
from actl.infrastructure.providers.simulator.adapter import SimulatorAdapter
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import SystemClock

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_runs_exactly_n_sessions_per_arm(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider = SimulatorAdapter(clock=SystemClock())
    clock = SystemClock()
    breaker = CircuitBreaker(name="growth-test", clock=clock)

    outcomes = await run_growth_simulation(
        session_factory, provider, clock, breaker, seed="test-seed-a", sessions=6
    )
    assert len(outcomes) == 12
    assert sum(1 for o in outcomes if o.arm == ARM_BASELINE) == 6
    assert sum(1 for o in outcomes if o.arm == ARM_UPSELL) == 6


async def test_same_seed_produces_the_same_stochastic_pattern(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """§28 P8: "reproducible growth measurement." Row identifiers differ
    between the two calls (fresh `new_id()` each time -- see
    `application.growth.simulation`'s own module docstring), but every
    stochastic decision (convert, offer, accept) must match exactly,
    session-for-session, both calls sharing the same seed."""
    provider = SimulatorAdapter(clock=SystemClock())
    clock = SystemClock()
    breaker = CircuitBreaker(name="growth-test", clock=clock)

    first = await run_growth_simulation(
        session_factory, provider, clock, breaker, seed="reproducible-seed", sessions=8
    )
    second = await run_growth_simulation(
        session_factory, provider, clock, breaker, seed="reproducible-seed", sessions=8
    )

    assert len(first) == len(second) == 16
    for a, b in zip(first, second, strict=True):
        assert a.arm == b.arm
        assert a.converted == b.converted
        assert a.upsell_offered == b.upsell_offered
        assert a.upsell_accepted == b.upsell_accepted
        # Whether an accepted upsell was actually admitted by the gate is
        # itself deterministic given identical mandate/catalog facts.
        assert (a.upsell_order_id is None) == (b.upsell_order_id is None)


async def test_only_the_upsell_arm_ever_offers_or_accepts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider = SimulatorAdapter(clock=SystemClock())
    clock = SystemClock()
    breaker = CircuitBreaker(name="growth-test", clock=clock)

    outcomes = await run_growth_simulation(
        session_factory, provider, clock, breaker, seed="arm-isolation-seed", sessions=10
    )
    baseline = [o for o in outcomes if o.arm == ARM_BASELINE]
    assert all(not o.upsell_offered and not o.upsell_accepted for o in baseline)


async def test_a_converted_session_always_has_a_base_order(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """§22.2: "Every accepted upsell is still just an order" -- reaches
    the real gate. A `converted=True` session's base purchase went
    through create_quote + handle_order_propose + the full saga to
    COMPLETED; the mandate's own bounds (900000+ budget vs. a 750000
    base purchase) guarantee the base purchase itself is never denied,
    so this is a strong, always-true invariant, not a probabilistic one."""
    provider = SimulatorAdapter(clock=SystemClock())
    clock = SystemClock()
    breaker = CircuitBreaker(name="growth-test", clock=clock)

    outcomes = await run_growth_simulation(
        session_factory, provider, clock, breaker, seed="base-order-seed", sessions=10
    )
    for o in outcomes:
        if o.converted:
            assert o.base_order_id is not None
        else:
            assert o.base_order_id is None
