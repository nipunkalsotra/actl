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
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from actl.infrastructure.db.models import OrderRow

# §18.2 comment: "CREATED|AUTHORIZED|CAPTURED|FAILED|COMPENSATED"
TERMINAL_STATUSES = ("CAPTURED", "FAILED", "COMPENSATED")


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
    provider_payment_id: str | None = None
    decline_reason: str | None = None
    source: str | None = None
    created_at: datetime | None = None


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
        provider_payment_id=row.provider_payment_id,
        decline_reason=row.decline_reason,
        source=row.source,
        created_at=row.created_at,
    )


class OrderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, order: OrderRecord) -> None:
        """`created_at` is taken from `order.created_at` when the caller
        supplies one, never from the column's `server_default=func.now()`
        — the reconciler compares `created_at` against an *injected*
        Clock (§28 P5), and a DB-side wall-clock timestamp would silently
        desync from a FrozenClock in tests (and from the real clock's
        instant during a slow request in production)."""
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
        if order.created_at is not None:
            row.created_at = order.created_at
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

    async def set_provider_order_id(
        self, order_id: str, provider_order_id: str, *, updated_at: datetime
    ) -> None:
        row = await self._session.get(OrderRow, order_id)
        if row is None:
            raise KeyError(order_id)
        row.provider_order_id = provider_order_id
        row.updated_at = updated_at

    async def list_non_terminal_older_than(
        self, cutoff: datetime, *, terminal_statuses: tuple[str, ...] = TERMINAL_STATUSES
    ) -> list[OrderRecord]:
        """§15.3 point 4 / §28 P5: the reconciler's own query — orders past
        `reconcile_after_s` still sitting in a non-terminal status."""
        result = await self._session.execute(
            select(OrderRow).where(
                OrderRow.status.notin_(terminal_statuses), OrderRow.created_at < cutoff
            )
        )
        return [order_row_to_record(row) for row in result.scalars()]

    async def set_source(self, order_id: str, source: str) -> None:
        """Tags a non-organic order (Demo Lab / growth simulation) so
        merchant KPIs and Live Orders never present it as real customer
        activity. Called only from application/demo.py and
        application/growth/simulation.py's own orchestration, after the
        shared gate/saga functions have already run -- never from
        gate.py/saga.py themselves."""
        row = await self._session.get(OrderRow, order_id)
        if row is None:
            raise KeyError(order_id)
        row.source = source

    async def count_and_sum_captured(self, *, organic_only: bool) -> tuple[int, int]:
        """Real gross sales / completed orders for merchant KPIs.
        `organic_only=True` excludes Demo Lab / growth-simulation-tagged
        rows (source IS NOT NULL) -- so a KPI never counts a merchant's
        own guarded demo clicks as real customer revenue."""
        stmt = select(func.count(), func.coalesce(func.sum(OrderRow.amount_minor), 0)).where(
            OrderRow.status == "CAPTURED"
        )
        if organic_only:
            stmt = stmt.where(OrderRow.source.is_(None))
        result = await self._session.execute(stmt)
        count, total = result.one()
        return int(count), int(total)

    async def list_recent(
        self, limit: int = 50, *, organic_only: bool | None = None
    ) -> list[OrderRecord]:
        """§28 P12 merchant live-orders view: the newest orders, read-only.
        No filtering by status here -- the caller (interfaces layer) decides
        what to show; this is just "most recent N", the same shape any
        operational order list needs.

        `organic_only` -- `None` (default): no source filter, every order.
        `True`: only `source IS NULL` (real buyer activity). `False`: only
        `source IS NOT NULL` (Demo Lab / growth-simulation rows) -- the same
        three-way convention `count_and_sum_captured` already uses, so a
        caller can request "Live operations" vs. "Demo activity" as two
        disjoint views instead of one badged, mixed list."""
        stmt = select(OrderRow).order_by(OrderRow.created_at.desc())
        if organic_only is True:
            stmt = stmt.where(OrderRow.source.is_(None))
        elif organic_only is False:
            stmt = stmt.where(OrderRow.source.is_not(None))
        stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        return [order_row_to_record(row) for row in result.scalars()]
