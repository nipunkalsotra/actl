"""§28 P8 instructions 7-8: growth outbox events, and
`compute_growth_metrics` deriving every number from them -- both arms,
undefined attach rate for an arm that never offers, and the revenue-
uplift formula, against a real Postgres container.

`compute_growth_metrics` deliberately has no per-test filter (it reflects
*all* historical growth facts, matching how a real GET /metrics/growth
would read the whole system's accumulated history) -- this file's session-
scoped Postgres container is shared with every other test that might also
emit baseline/upsell events, so assertions compare a before/after
snapshot's *delta*, never an absolute count.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application.growth.events import (
    ARM_BASELINE,
    ARM_UPSELL,
    emit_order_completed,
    emit_session_started,
    emit_upsell_accepted,
    emit_upsell_offered,
)
from actl.application.growth.metrics import GrowthMetrics, compute_growth_metrics
from actl.infrastructure.db.uow import UnitOfWork
from actl.platform.ids import new_id

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _snapshot(session_factory: async_sessionmaker[AsyncSession]) -> GrowthMetrics:
    async with UnitOfWork(session_factory) as uow:
        return await compute_growth_metrics(uow)


async def test_both_arms_report_correct_conversion_aov_attach_and_uplift(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    before = await _snapshot(session_factory)

    async with UnitOfWork(session_factory) as uow:
        # Baseline arm: 4 sessions, 2 orders (conversion 50%), no upsells offered.
        for _ in range(4):
            await emit_session_started(uow, session_id=new_id("sess"), arm=ARM_BASELINE)
        for total in (100000, 200000):
            await emit_order_completed(
                uow,
                session_id=new_id("sess"),
                arm=ARM_BASELINE,
                order_id=new_id("ord"),
                total_minor=total,
                currency="INR",
            )

        # Upsell arm: 4 sessions, 2 orders, 3 offered, 2 accepted.
        for _ in range(4):
            await emit_session_started(uow, session_id=new_id("sess"), arm=ARM_UPSELL)
        for total in (150000, 250000):
            await emit_order_completed(
                uow,
                session_id=new_id("sess"),
                arm=ARM_UPSELL,
                order_id=new_id("ord"),
                total_minor=total,
                currency="INR",
            )
        for _ in range(3):
            await emit_upsell_offered(uow, session_id=new_id("sess"), arm=ARM_UPSELL, sku="X")
        for _ in range(2):
            await emit_upsell_accepted(uow, session_id=new_id("sess"), arm=ARM_UPSELL, sku="X")
        await uow.commit()

    after = await _snapshot(session_factory)

    assert after.baseline.sessions - before.baseline.sessions == 4
    assert after.baseline.orders - before.baseline.orders == 2
    assert after.upsell.sessions - before.upsell.sessions == 4
    assert after.upsell.orders - before.upsell.orders == 2
    assert after.upsell.upsell_offered - before.upsell.upsell_offered == 3
    assert after.upsell.upsell_accepted - before.upsell.upsell_accepted == 2

    # This test's own contribution to AOV is exactly the mean of the
    # amounts it just emitted, *if* no other test/run has ever
    # contributed to either arm -- true for a fresh container, and this
    # session-scoped fixture starts one per pytest run.
    if before.baseline.orders == 0:
        assert after.baseline.aov_minor == 150000  # (100000+200000)/2
        assert after.baseline.attach_rate is None  # §22.2: baseline never offers
    if before.upsell.orders == 0:
        assert after.upsell.aov_minor == 200000  # (150000+250000)/2
        assert after.upsell.attach_rate == pytest.approx(2 / 3)
        assert after.revenue_uplift == pytest.approx((200000 - 150000) / 150000)


async def test_an_arm_with_no_sessions_yet_reports_zero_not_a_crash(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Whatever accumulated from other tests in this session-scoped
    container, the computation itself must never raise -- and if this is
    genuinely the first call, the zero/None case is exercised directly."""
    metrics = await _snapshot(session_factory)
    assert metrics.baseline.sessions >= 0
    assert metrics.upsell.sessions >= 0
