"""Payment repository (§18.2). §18.2 has one `orders` table, not a separate
`orders` + `payments` pair — payment lifecycle (status, provider_order_id,
idempotency_key) already lives on that table. This repository is a
payment-shaped view over the same rows `orders.py` writes, per §6.2's
"order: Order and payment aggregates" (one module owns both). See ADR 0003.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from actl.infrastructure.db.models import OrderRow
from actl.infrastructure.db.repositories.orders import OrderRecord, order_row_to_record


class PaymentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_provider_order_id(self, provider_order_id: str) -> OrderRecord | None:
        result = await self._session.execute(
            select(OrderRow).where(OrderRow.provider_order_id == provider_order_id)
        )
        row = result.scalar_one_or_none()
        return order_row_to_record(row) if row is not None else None

    async def mark_captured(self, order_id: str, provider_order_id: str) -> None:
        row = await self._session.get(OrderRow, order_id)
        if row is None:
            raise KeyError(order_id)
        row.status = "CAPTURED"
        row.provider_order_id = provider_order_id
