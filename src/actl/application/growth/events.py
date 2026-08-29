"""§28 P8 instruction 7 / §22.2 / Appendix B: the four growth-instrumentation
outbox events, on the same transactional outbox everything else uses.
`arm` is either "baseline" (upsell-off) or "upsell" (upsell-on) --
GET /metrics/growth and `actl growth` both group by exactly this field.
"""

from __future__ import annotations

from actl.infrastructure.db.repositories.outbox import OutboxRecord
from actl.infrastructure.db.uow import UnitOfWork

ARM_BASELINE = "baseline"
ARM_UPSELL = "upsell"


async def emit_session_started(uow: UnitOfWork, *, session_id: str, arm: str) -> None:
    await uow.outbox.add(
        OutboxRecord(
            aggregate="session",
            aggregate_id=session_id,
            event_type="session.started",
            payload={"session_id": session_id, "arm": arm},
        )
    )


async def emit_upsell_offered(uow: UnitOfWork, *, session_id: str, arm: str, sku: str) -> None:
    await uow.outbox.add(
        OutboxRecord(
            aggregate="session",
            aggregate_id=session_id,
            event_type="upsell.offered",
            payload={"session_id": session_id, "arm": arm, "sku": sku},
        )
    )


async def emit_upsell_accepted(uow: UnitOfWork, *, session_id: str, arm: str, sku: str) -> None:
    await uow.outbox.add(
        OutboxRecord(
            aggregate="session",
            aggregate_id=session_id,
            event_type="upsell.accepted",
            payload={"session_id": session_id, "arm": arm, "sku": sku},
        )
    )


async def emit_order_completed(
    uow: UnitOfWork,
    *,
    session_id: str,
    arm: str,
    order_id: str,
    total_minor: int,
    currency: str,
) -> None:
    """§22.2: "Every accepted upsell is still just an order." This is the
    *only* event that feeds AOV/conversion -- always the amount the ledger
    actually settled, never a value invented for the metrics."""
    await uow.outbox.add(
        OutboxRecord(
            aggregate="session",
            aggregate_id=session_id,
            event_type="order.completed",
            payload={
                "session_id": session_id,
                "arm": arm,
                "order_id": order_id,
                "total_minor": total_minor,
                "currency": currency,
            },
        )
    )
