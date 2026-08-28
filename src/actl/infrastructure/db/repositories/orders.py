"""Order repository (§18.2 `orders`).

No P1 domain model exists for an order yet — `domain/order/` (order *and*
payment aggregates together, per §6.2) is not a P1 deliverable. `OrderRecord`
is a local, infrastructure-only record; redirect this repository to a real
domain model once that module lands. `payments.py`'s `PaymentRepository`
operates on this same table (§18.2 has one `orders` table, no separate
`payments` table — its `status`/`provider_order_id`/`idempotency_key`
columns already carry the payment lifecycle).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from actl.infrastructure.db.models import OrderRow


@dataclass(frozen=True)
class OrderRecord:
    id: str
    mandate_id: str
    decision_id: str
    quote_id: str
    status: str
    amount_minor: int
    currency: str
    attempt_no: int
    idempotency_key: str
    provider_order_id: str | None = None


def order_row_to_record(row: OrderRow) -> OrderRecord:
    return OrderRecord(
        id=row.id,
        mandate_id=row.mandate_id,
        decision_id=row.decision_id,
        quote_id=row.quote_id,
        status=row.status,
        amount_minor=row.amount_minor,
        currency=row.currency,
        attempt_no=row.attempt_no,
        idempotency_key=row.idempotency_key,
        provider_order_id=row.provider_order_id,
    )


class OrderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, order: OrderRecord) -> None:
        row = OrderRow(
            id=order.id,
            mandate_id=order.mandate_id,
            decision_id=order.decision_id,
            quote_id=order.quote_id,
            status=order.status,
            amount_minor=order.amount_minor,
            currency=order.currency,
            attempt_no=order.attempt_no,
            idempotency_key=order.idempotency_key,
            provider_order_id=order.provider_order_id,
        )
        self._session.add(row)

    async def get(self, order_id: str) -> OrderRecord | None:
        row = await self._session.get(OrderRow, order_id)
        return order_row_to_record(row) if row is not None else None

    async def get_by_idempotency_key(self, idempotency_key: str) -> OrderRecord | None:
        result = await self._session.execute(
            select(OrderRow).where(OrderRow.idempotency_key == idempotency_key)
        )
        row = result.scalar_one_or_none()
        return order_row_to_record(row) if row is not None else None
