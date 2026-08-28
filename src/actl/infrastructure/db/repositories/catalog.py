"""Catalog repository (§13.1 — not in §18.2's excerpt, added in P4; see
docs/adr/0005-p4-catalog-quote-decisions.md). Pagination uses a keyset
(unit_price_minor, sku) cursor rather than OFFSET: immune to duplicate or
skipped rows when items are inserted/updated between pages (§28 P4
instruction 2's "stable ordering explicit so paging cannot duplicate or
skip records")."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from actl.infrastructure.db.models import CatalogItemRow, CatalogMetaRow


@dataclass(frozen=True)
class CatalogItemRecord:
    sku: str
    category: str
    merchant_id: str
    unit: str
    unit_price_minor: int
    available_units: int
    location_city: str
    location_country: str
    rating: float
    sea_facing: bool
    breakfast_included: bool
    refundable: bool
    cancellation_window_h: int
    instant_confirm: bool
    taxes_included: bool
    quote_required: bool
    version: int


def _to_record(row: CatalogItemRow) -> CatalogItemRecord:
    return CatalogItemRecord(
        sku=row.sku,
        category=row.category,
        merchant_id=row.merchant_id,
        unit=row.unit,
        unit_price_minor=row.unit_price_minor,
        available_units=row.available_units,
        location_city=row.location_city,
        location_country=row.location_country,
        rating=row.rating,
        sea_facing=row.sea_facing,
        breakfast_included=row.breakfast_included,
        refundable=row.refundable,
        cancellation_window_h=row.cancellation_window_h,
        instant_confirm=row.instant_confirm,
        taxes_included=row.taxes_included,
        quote_required=row.quote_required,
        version=row.version,
    )


class CatalogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_item(self, item: CatalogItemRecord) -> None:
        """scripts/seed.py only — idempotent seeding. The live price
        mutation path is mutate_price() below, which also bumps the global
        version; this does not."""
        row = await self._session.get(CatalogItemRow, item.sku)
        if row is None:
            row = CatalogItemRow(sku=item.sku)
            self._session.add(row)
        row.category = item.category
        row.merchant_id = item.merchant_id
        row.unit = item.unit
        row.unit_price_minor = item.unit_price_minor
        row.available_units = item.available_units
        row.location_city = item.location_city
        row.location_country = item.location_country
        row.rating = item.rating
        row.sea_facing = item.sea_facing
        row.breakfast_included = item.breakfast_included
        row.refundable = item.refundable
        row.cancellation_window_h = item.cancellation_window_h
        row.instant_confirm = item.instant_confirm
        row.taxes_included = item.taxes_included
        row.quote_required = item.quote_required
        row.version = item.version

    async def get_item(self, sku: str) -> CatalogItemRecord | None:
        row = await self._session.get(CatalogItemRow, sku)
        return _to_record(row) if row is not None else None

    async def current_version(self) -> int:
        result = await self._session.execute(
            select(CatalogMetaRow.version).where(CatalogMetaRow.id == "default")
        )
        version = result.scalar_one_or_none()
        if version is None:
            raise RuntimeError(
                "catalog_meta has no 'default' row -- migration 0003 not applied?"
            )
        return version

    async def list_items(
        self,
        *,
        category: str | None = None,
        location_city: str | None = None,
        location_country: str | None = None,
        max_unit_minor: int | None = None,
        cursor: tuple[int, str] | None = None,
        limit: int = 20,
    ) -> list[CatalogItemRecord]:
        """Stable order: (unit_price_minor, sku) ascending. `cursor`, if
        given, is the (unit_price_minor, sku) of the last item on the
        previous page. Returns up to `limit` rows -- callers wanting to
        detect a further page should request `limit + 1` and trim."""
        stmt = select(CatalogItemRow).order_by(
            CatalogItemRow.unit_price_minor, CatalogItemRow.sku
        )
        if category is not None:
            stmt = stmt.where(CatalogItemRow.category == category)
        if location_city is not None:
            stmt = stmt.where(CatalogItemRow.location_city == location_city)
        if location_country is not None:
            stmt = stmt.where(CatalogItemRow.location_country == location_country)
        if max_unit_minor is not None:
            stmt = stmt.where(CatalogItemRow.unit_price_minor <= max_unit_minor)
        if cursor is not None:
            cursor_price, cursor_sku = cursor
            stmt = stmt.where(
                tuple_(CatalogItemRow.unit_price_minor, CatalogItemRow.sku)
                > (cursor_price, cursor_sku)
            )
        stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        return [_to_record(row) for row in result.scalars()]

    async def mutate_price(self, sku: str, new_unit_price_minor: int) -> CatalogItemRecord:
        """Demo-only admin mutation (§28 P4). Bumps the global catalog
        version and stamps this item with it, in the caller's transaction —
        both writes commit together or neither does. Raises KeyError if the
        sku does not exist."""
        item_row = await self._session.get(CatalogItemRow, sku)
        if item_row is None:
            raise KeyError(sku)

        result = await self._session.execute(
            update(CatalogMetaRow)
            .where(CatalogMetaRow.id == "default")
            .values(version=CatalogMetaRow.version + 1)
            .returning(CatalogMetaRow.version)
        )
        new_version = result.scalar_one()

        item_row.unit_price_minor = new_unit_price_minor
        item_row.version = new_version
        return _to_record(item_row)
