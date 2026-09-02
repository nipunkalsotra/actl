"""AddonPurchaseRepository (§28 P12 contextual upsell). Single source of
truth for the real, buyer-driven post-booking add-on flow -- both the
duplicate-purchase guard and the offered/accepted/settled/declined
counters merchant metrics read from. See migration 0010's docstring.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from actl.infrastructure.db.models import AddonPurchaseRow

STATUS_OFFERED = "offered"
STATUS_PENDING = "pending"
STATUS_SETTLED = "settled"
STATUS_FAILED = "failed"
STATUS_DECLINED = "declined"


@dataclass(frozen=True)
class AddonPurchaseRecord:
    id: str
    base_order_id: str
    offer_sku: str
    status: str
    addon_mandate_id: str | None
    addon_order_id: str | None
    price_minor: int
    currency: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


def _to_record(row: AddonPurchaseRow) -> AddonPurchaseRecord:
    return AddonPurchaseRecord(
        id=row.id,
        base_order_id=row.base_order_id,
        offer_sku=row.offer_sku,
        status=row.status,
        addon_mandate_id=row.addon_mandate_id,
        addon_order_id=row.addon_order_id,
        price_minor=row.price_minor,
        currency=row.currency,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class AddonPurchaseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record_offered(
        self, *, id: str, base_order_id: str, offer_sku: str, price_minor: int, currency: str
    ) -> None:
        """Idempotent: a re-fetch of the same eligible offer (e.g. the
        buyer reopens chat) must never fail or duplicate -- the UNIQUE
        constraint on (base_order_id, offer_sku) makes this safe to call
        every time an offer is shown, ON CONFLICT DO NOTHING."""
        stmt = (
            pg_insert(AddonPurchaseRow)
            .values(
                id=id,
                base_order_id=base_order_id,
                offer_sku=offer_sku,
                status=STATUS_OFFERED,
                price_minor=price_minor,
                currency=currency,
            )
            .on_conflict_do_nothing(constraint="uq_addon_purchases_base_offer")
        )
        await self._session.execute(stmt)

    async def get(self, base_order_id: str, offer_sku: str) -> AddonPurchaseRecord | None:
        result = await self._session.execute(
            select(AddonPurchaseRow).where(
                AddonPurchaseRow.base_order_id == base_order_id,
                AddonPurchaseRow.offer_sku == offer_sku,
            )
        )
        row = result.scalar_one_or_none()
        return _to_record(row) if row is not None else None

    async def try_mark_pending(
        self,
        *,
        id: str,
        base_order_id: str,
        offer_sku: str,
        addon_mandate_id: str,
        price_minor: int,
        currency: str,
    ) -> bool:
        """The duplicate-purchase guard, and the whole reason
        addon_purchases exists: an atomic upsert, race-safe under a
        concurrent double-click (Postgres row-level locking on the
        UNIQUE(base_order_id, offer_sku) conflict target). Creates the
        row directly as 'pending' if none exists yet (a purchase called
        without a prior GET /offers call is still safe, just skips the
        'offered' bookkeeping step), or transitions an existing 'offered'
        row to 'pending'. Any other existing status (pending/settled/
        failed/declined) means "already acted on" -- the WHERE clause on
        the conflict update excludes it, so no row is returned and the
        caller must never proceed to build a mandate/quote/purchase."""
        stmt = (
            pg_insert(AddonPurchaseRow)
            .values(
                id=id,
                base_order_id=base_order_id,
                offer_sku=offer_sku,
                status=STATUS_PENDING,
                addon_mandate_id=addon_mandate_id,
                price_minor=price_minor,
                currency=currency,
            )
            .on_conflict_do_update(
                constraint="uq_addon_purchases_base_offer",
                set_={
                    "status": STATUS_PENDING,
                    "addon_mandate_id": addon_mandate_id,
                    "updated_at": func.now(),
                },
                where=(AddonPurchaseRow.status == STATUS_OFFERED),
            )
            .returning(AddonPurchaseRow.id)
        )
        result = await self._session.execute(stmt)
        return result.first() is not None

    async def mark_settled(
        self, *, base_order_id: str, offer_sku: str, addon_order_id: str
    ) -> None:
        await self._session.execute(
            update(AddonPurchaseRow)
            .where(
                AddonPurchaseRow.base_order_id == base_order_id,
                AddonPurchaseRow.offer_sku == offer_sku,
            )
            .values(status=STATUS_SETTLED, addon_order_id=addon_order_id, updated_at=func.now())
        )

    async def mark_failed(self, *, base_order_id: str, offer_sku: str) -> None:
        await self._session.execute(
            update(AddonPurchaseRow)
            .where(
                AddonPurchaseRow.base_order_id == base_order_id,
                AddonPurchaseRow.offer_sku == offer_sku,
            )
            .values(status=STATUS_FAILED, updated_at=func.now())
        )

    async def decline_all_offered(self, base_order_id: str) -> None:
        """"No thanks" -- only rows still in 'offered' (never acted on)
        move to 'declined'; an already-settled/failed add-on for the same
        base order is untouched."""
        await self._session.execute(
            update(AddonPurchaseRow)
            .where(
                AddonPurchaseRow.base_order_id == base_order_id,
                AddonPurchaseRow.status == STATUS_OFFERED,
            )
            .values(status=STATUS_DECLINED, updated_at=func.now())
        )

    async def list_for_base_order(self, base_order_id: str) -> list[AddonPurchaseRecord]:
        result = await self._session.execute(
            select(AddonPurchaseRow).where(AddonPurchaseRow.base_order_id == base_order_id)
        )
        return [_to_record(row) for row in result.scalars()]

    async def count_by_status(self, status: str) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(AddonPurchaseRow)
            .where(AddonPurchaseRow.status == status)
        )
        return int(result.scalar_one())

    async def count_by_statuses(self, statuses: tuple[str, ...]) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(AddonPurchaseRow)
            .where(AddonPurchaseRow.status.in_(statuses))
        )
        return int(result.scalar_one())

    async def count_all(self) -> int:
        """Every (base_order, offer) pair ever shown or attempted -- the
        real "offered" denominator for attach-rate, regardless of which
        status it's since moved to."""
        result = await self._session.execute(select(func.count()).select_from(AddonPurchaseRow))
        return int(result.scalar_one())

    async def sum_price_minor_by_status(self, status: str) -> int:
        result = await self._session.execute(
            select(func.coalesce(func.sum(AddonPurchaseRow.price_minor), 0)).where(
                AddonPurchaseRow.status == status
            )
        )
        return int(result.scalar_one())
