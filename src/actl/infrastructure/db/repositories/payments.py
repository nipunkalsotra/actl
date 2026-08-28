"""Payment repository (§18.2). §18.2 has one `orders` table, not a separate
`orders` + `payments` pair — payment lifecycle (status, provider_order_id,
idempotency_key) already lives on that table. This repository is a
payment-shaped view over the same rows `orders.py` writes, per §6.2's
"order: Order and payment aggregates" (one module owns both). See ADR 0003.
"""

from __future__ import annotations

from datetime import datetime

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

    async def transition_status(
        self,
        order_id: str,
        status: str,
        *,
        updated_at: datetime,
        provider_payment_id: str | None = None,
        decline_reason: str | None = None,
    ) -> None:
        """AUTHORIZED/CAPTURED/FAILED/COMPENSATED — the payment-outcome
        half of an order's lifecycle (§12.2). `provider_payment_id`/
        `decline_reason` are set only when the caller actually has a new
        value; omitting them leaves whatever was already recorded."""
        row = await self._session.get(OrderRow, order_id)
        if row is None:
            raise KeyError(order_id)
        row.status = status
        row.updated_at = updated_at
        if provider_payment_id is not None:
            row.provider_payment_id = provider_payment_id
        if decline_reason is not None:
            row.decline_reason = decline_reason
