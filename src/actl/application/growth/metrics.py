"""§22.2 growth instrumentation. Every number here is derived from
persisted outbox facts (`OutboxRepository.count_by_event_type_and_arm`/
`sum_order_total_minor`) -- nothing here is computed from an in-memory
count or a fabricated value; both arms are queried by the exact same
formula, only the `arm` filter differs.
"""

from __future__ import annotations

from dataclasses import dataclass

from actl.application.growth.events import ARM_BASELINE, ARM_UPSELL
from actl.infrastructure.db.uow import UnitOfWork


@dataclass(frozen=True)
class ArmMetrics:
    arm: str
    sessions: int
    orders: int
    conversion_rate: float
    aov_minor: int | None
    upsell_offered: int
    upsell_accepted: int
    attach_rate: float | None


@dataclass(frozen=True)
class GrowthMetrics:
    baseline: ArmMetrics
    upsell: ArmMetrics
    revenue_uplift: float | None


async def _arm_metrics(uow: UnitOfWork, arm: str) -> ArmMetrics:
    sessions = await uow.outbox.count_by_event_type_and_arm("session.started", arm)
    orders = await uow.outbox.count_by_event_type_and_arm("order.completed", arm)
    offered = await uow.outbox.count_by_event_type_and_arm("upsell.offered", arm)
    accepted = await uow.outbox.count_by_event_type_and_arm("upsell.accepted", arm)
    total_minor = await uow.outbox.sum_order_total_minor(arm)

    conversion_rate = orders / sessions if sessions else 0.0
    aov_minor = total_minor // orders if orders else None
    # §22.2: "Undefined -- upsell-off arm never offers." None, not 0 or
    # NaN, so a caller can render "n/a" rather than a misleading 0%.
    attach_rate = accepted / offered if offered else None

    return ArmMetrics(
        arm=arm,
        sessions=sessions,
        orders=orders,
        conversion_rate=conversion_rate,
        aov_minor=aov_minor,
        upsell_offered=offered,
        upsell_accepted=accepted,
        attach_rate=attach_rate,
    )


async def compute_growth_metrics(uow: UnitOfWork) -> GrowthMetrics:
    """§22.2: "GET /metrics/growth?window= -- returns both arms, the four
    numbers, and the sample size, so the claim is checkable rather than
    asserted." (This build has no time-windowed session table to filter
    by `window` against -- see docs/adr/0009 for that scope decision;
    every session/order the outbox has ever recorded is included.)"""
    baseline = await _arm_metrics(uow, ARM_BASELINE)
    upsell = await _arm_metrics(uow, ARM_UPSELL)

    revenue_uplift: float | None = None
    if baseline.aov_minor and upsell.aov_minor:
        revenue_uplift = (upsell.aov_minor - baseline.aov_minor) / baseline.aov_minor

    return GrowthMetrics(baseline=baseline, upsell=upsell, revenue_uplift=revenue_uplift)
