"""§22.2: "A GET /metrics/growth endpoint returns the current values as
JSON... returns both arms, the four numbers, and the sample size, so the
claim is checkable rather than asserted." Every value comes straight from
`application.growth.metrics.compute_growth_metrics`, which derives them
from persisted outbox facts -- this router does no computation of its
own, only serialisation.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from actl.application.growth.metrics import ArmMetrics, compute_growth_metrics
from actl.infrastructure.db.uow import UnitOfWork
from actl.interfaces.http.deps import get_uow

router = APIRouter()


def _arm_json(arm: ArmMetrics) -> dict[str, Any]:
    return {
        "arm": arm.arm,
        "sessions": arm.sessions,
        "orders": arm.orders,
        "conversion_rate": arm.conversion_rate,
        "aov_minor": arm.aov_minor,
        "upsell_offered": arm.upsell_offered,
        "upsell_accepted": arm.upsell_accepted,
        "attach_rate": arm.attach_rate,
    }


@router.get("/metrics/growth")
async def get_growth_metrics(uow: UnitOfWork = Depends(get_uow)) -> dict[str, Any]:
    metrics = await compute_growth_metrics(uow)
    return {
        "baseline": _arm_json(metrics.baseline),
        "upsell": _arm_json(metrics.upsell),
        "revenue_uplift": metrics.revenue_uplift,
    }
