"""§28 P4: seeds the demo catalog with Goa hotel SKUs (§13.1). Idempotent —
upsert_item() updates in place, so `make seed` is safe to re-run. Prices,
ratings, refundability and stock deliberately vary so a later ranking
phase (P8) and this phase's own filter/paginate tests have something to
distinguish between; HTL-GOA-SEA-DLX / mrc_seabreeze matches §13.1's own
worked example exactly.
"""

from __future__ import annotations

import asyncio

from actl.infrastructure.db.repositories.catalog import CatalogItemRecord
from actl.infrastructure.db.uow import UnitOfWork

_ITEMS = [
    CatalogItemRecord(
        sku="HTL-GOA-SEA-DLX",
        category="travel.hotel",
        merchant_id="mrc_seabreeze",
        unit="night",
        unit_price_minor=280000,
        available_units=6,
        location_city="Goa",
        location_country="IN",
        rating=4.4,
        sea_facing=True,
        breakfast_included=True,
        refundable=True,
        cancellation_window_h=48,
        instant_confirm=True,
        taxes_included=True,
        quote_required=True,
        version=1,
    ),
    CatalogItemRecord(
        sku="HTL-GOA-SUNSET-STD",
        category="travel.hotel",
        merchant_id="mrc_sunsetview",
        unit="night",
        unit_price_minor=180000,
        available_units=10,
        location_city="Goa",
        location_country="IN",
        rating=3.9,
        sea_facing=False,
        breakfast_included=False,
        refundable=False,
        cancellation_window_h=0,
        instant_confirm=True,
        taxes_included=True,
        quote_required=True,
        version=1,
    ),
    CatalogItemRecord(
        sku="HTL-GOA-PALM-STE",
        category="travel.hotel",
        merchant_id="mrc_palmgrove",
        unit="night",
        unit_price_minor=420000,
        available_units=3,
        location_city="Goa",
        location_country="IN",
        rating=4.7,
        sea_facing=True,
        breakfast_included=True,
        refundable=True,
        cancellation_window_h=72,
        instant_confirm=False,
        taxes_included=True,
        quote_required=True,
        version=1,
    ),
    CatalogItemRecord(
        sku="HTL-GOA-BUDGET-RM",
        category="travel.hotel",
        merchant_id="mrc_budgetstay",
        unit="night",
        unit_price_minor=95000,
        available_units=15,
        location_city="Goa",
        location_country="IN",
        rating=3.5,
        sea_facing=False,
        breakfast_included=False,
        refundable=True,
        cancellation_window_h=24,
        instant_confirm=True,
        taxes_included=False,
        quote_required=True,
        version=1,
    ),
    CatalogItemRecord(
        sku="HTL-GOA-CLIFF-VIL",
        category="travel.hotel",
        merchant_id="mrc_cliffside",
        unit="night",
        unit_price_minor=550000,
        available_units=2,
        location_city="Goa",
        location_country="IN",
        rating=4.9,
        sea_facing=True,
        breakfast_included=True,
        refundable=False,
        cancellation_window_h=0,
        instant_confirm=True,
        taxes_included=True,
        quote_required=True,
        version=1,
    ),
    CatalogItemRecord(
        sku="HTL-GOA-SOLDOUT-RM",
        category="travel.hotel",
        merchant_id="mrc_seabreeze",
        unit="night",
        unit_price_minor=210000,
        available_units=0,
        location_city="Goa",
        location_country="IN",
        rating=4.0,
        sea_facing=False,
        breakfast_included=True,
        refundable=True,
        cancellation_window_h=48,
        instant_confirm=True,
        taxes_included=True,
        quote_required=True,
        version=1,
    ),
]


async def seed() -> None:
    async with UnitOfWork() as uow:
        for item in _ITEMS:
            await uow.catalog.upsert_item(item)
        await uow.commit()
    print(f"seeded {len(_ITEMS)} catalog items")


def main() -> None:
    asyncio.run(seed())


if __name__ == "__main__":
    main()
