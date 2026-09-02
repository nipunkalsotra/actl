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

# §28 P12 contextual upsell: demo partner add-on inventory, not live
# external hotel supply -- see README's "Demo partner inventory" section.
# Reuses CatalogItemRecord/create_quote/gate/saga unchanged (§28 P12's own
# design note): `category` doubles as the pricing-shape signal the upsell
# eligibility endpoint reads to decide what to multiply `unit_price_minor`
# by when calling create_quote (nights, guests*nights, or 1 for a flat
# fee) -- no schema change needed to express three different pricing
# shapes. Hotel-specific attribute fields that don't really describe an
# add-on (rating, sea_facing) are set to neutral defaults and never shown
# for these SKUs in the buyer UI.
_ADDON_ITEMS = [
    CatalogItemRecord(
        sku="ADDON-BREAKFAST-GOA",
        category="travel.addon.per_guest_per_night",
        merchant_id="mrc_goa_partners",
        unit="guest_night",
        unit_price_minor=35000,  # Rs 350 per guest per night
        available_units=200,
        location_city="Goa",
        location_country="IN",
        rating=0.0,
        sea_facing=False,
        breakfast_included=False,
        refundable=True,
        cancellation_window_h=24,
        instant_confirm=True,
        taxes_included=True,
        quote_required=True,
        version=1,
    ),
    CatalogItemRecord(
        sku="ADDON-AIRPORT-PICKUP-GOA",
        category="travel.addon.flat",
        merchant_id="mrc_goa_partners",
        unit="trip",
        unit_price_minor=120000,  # Rs 1,200 flat
        available_units=30,
        location_city="Goa",
        location_country="IN",
        rating=0.0,
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
        sku="ADDON-ROOM-UPGRADE-GOA",
        category="travel.addon.per_night",
        merchant_id="mrc_goa_partners",
        unit="night",
        unit_price_minor=150000,  # Rs 1,500 per night
        available_units=15,
        location_city="Goa",
        location_country="IN",
        rating=0.0,
        sea_facing=False,
        breakfast_included=False,
        refundable=True,
        cancellation_window_h=24,
        instant_confirm=True,
        taxes_included=True,
        quote_required=True,
        version=1,
    ),
]


async def seed() -> None:
    async with UnitOfWork() as uow:
        for item in _ITEMS + _ADDON_ITEMS:
            await uow.catalog.upsert_item(item)
        await uow.commit()
    print(f"seeded {len(_ITEMS)} catalog items and {len(_ADDON_ITEMS)} demo add-on offers")


def main() -> None:
    asyncio.run(seed())


if __name__ == "__main__":
    main()
