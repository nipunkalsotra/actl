"""§28 P12 contextual upsell: eligibility + pricing for real, buyer-driven
post-booking add-on offers. Deterministic, no LLM call anywhere in this
module -- eligibility, pricing, and authorization are server-side facts,
never model output (an LLM may only reword what this module already
decided, in the interfaces/presentation layer, never here).

Add-on offers are ordinary `catalog_items` rows (category
"travel.addon.flat" | "travel.addon.per_night" |
"travel.addon.per_guest_per_night", seeded in scripts/seed.py) so the real
`create_quote` -> gate -> saga -> ledger -> payment pipeline needs no
changes at all to sell one: the category also doubles as the pricing-shape
signal this module reads to decide what multiplier to pass as
`create_quote`'s `nights` argument. See scripts/seed.py's own comment for
why this reuse is deliberate, not a shortcut.
"""

from __future__ import annotations

from dataclasses import dataclass

from actl.infrastructure.db.repositories.addon_purchases import STATUS_OFFERED
from actl.infrastructure.db.repositories.catalog import CatalogItemRecord
from actl.infrastructure.db.uow import UnitOfWork
from actl.platform.ids import new_id

_ADDON_CATEGORY_PREFIX = "travel.addon."
_HOTEL_CATEGORY = "travel.hotel"

_OFFER_TITLES: dict[str, str] = {
    "ADDON-BREAKFAST-GOA": "Daily breakfast",
    "ADDON-AIRPORT-PICKUP-GOA": "Airport pickup",
    "ADDON-ROOM-UPGRADE-GOA": "Room upgrade",
}


@dataclass(frozen=True)
class BaseOrderContext:
    mandate_id: str
    nights: int
    rooms: int
    location_city: str
    location_country: str


@dataclass(frozen=True)
class EligibleOffer:
    sku: str
    category: str
    title: str
    unit_price_minor: int
    total_minor: int
    currency: str
    refundable: bool
    quantity_description: str
    # The exact multiplier `total_minor = unit_price_minor * quantity` --
    # also exactly what a purchase must pass as create_quote's `nights`
    # argument, so the quote it pins totals to this same `total_minor`.
    quantity: int


def _pricing_multiplier(category: str, *, nights: int, rooms: int) -> int | None:
    """None means "not a recognised add-on pricing shape" -- never falls
    back to a default multiplier."""
    if category == f"{_ADDON_CATEGORY_PREFIX}flat":
        return 1
    if category == f"{_ADDON_CATEGORY_PREFIX}per_night":
        return nights
    if category == f"{_ADDON_CATEGORY_PREFIX}per_guest_per_night":
        return nights * rooms
    return None


def _describe_quantity(category: str, *, nights: int, rooms: int) -> str:
    if category == f"{_ADDON_CATEGORY_PREFIX}flat":
        return "one-time"
    if category == f"{_ADDON_CATEGORY_PREFIX}per_night":
        return f"{nights} night{'s' if nights != 1 else ''}"
    if category == f"{_ADDON_CATEGORY_PREFIX}per_guest_per_night":
        plural_r = "s" if rooms != 1 else ""
        plural_n = "s" if nights != 1 else ""
        return f"{rooms} guest{plural_r} x {nights} night{plural_n}"
    return ""


def _to_offer(addon: CatalogItemRecord, *, nights: int, rooms: int) -> EligibleOffer | None:
    multiplier = _pricing_multiplier(addon.category, nights=nights, rooms=rooms)
    if multiplier is None or multiplier <= 0:
        return None
    return EligibleOffer(
        sku=addon.sku,
        category=addon.category,
        title=_OFFER_TITLES.get(addon.sku, addon.sku),
        unit_price_minor=addon.unit_price_minor,
        total_minor=addon.unit_price_minor * multiplier,
        currency="INR",
        refundable=addon.refundable,
        quantity_description=_describe_quantity(addon.category, nights=nights, rooms=rooms),
        quantity=multiplier,
    )


async def _load_base_context(uow: UnitOfWork, base_order_id: str) -> BaseOrderContext | None:
    """None if the base order isn't upsell-eligible at all: not found, not
    yet CAPTURED, or not a real travel.hotel purchase this router knows
    how to contextualise an add-on for."""
    order = await uow.orders.get(base_order_id)
    if order is None or order.status != "CAPTURED":
        return None
    quote = await uow.quotes.get(order.quote_id)
    if quote is None:
        return None
    item = await uow.catalog.get_item(quote.sku)
    if item is None or item.category != _HOTEL_CATEGORY:
        return None
    loaded = await uow.mandates.get(order.mandate_id)
    if loaded is None:
        return None
    mandate, _status = loaded
    return BaseOrderContext(
        mandate_id=mandate.mandate_id,
        nights=mandate.intent.nights,
        rooms=mandate.intent.rooms,
        location_city=item.location_city,
        location_country=item.location_country,
    )


async def _addon_catalog_items(uow: UnitOfWork, ctx: BaseOrderContext) -> list[CatalogItemRecord]:
    all_items = await uow.catalog.list_items(
        location_city=ctx.location_city, location_country=ctx.location_country, limit=50
    )
    return [i for i in all_items if i.category.startswith(_ADDON_CATEGORY_PREFIX)]


async def list_eligible_offers(
    uow: UnitOfWork, *, base_order_id: str
) -> tuple[str, list[EligibleOffer]] | None:
    """Returns (currency, offers) -- an empty `offers` list is a genuine,
    honest "nothing eligible right now" (out of stock, or every add-on
    already purchased/declined for this booking). Returns None only when
    the base order itself can never have an upsell shown (not found, not
    settled, not a hotel booking) -- distinct so the caller never renders
    a teaser card in that case either.

    As a side effect, records an 'offered' row (idempotent, ON CONFLICT DO
    NOTHING) for each currently-eligible offer -- the real "was this shown
    to the buyer" signal merchant KPIs' attach-rate denominator reads."""
    ctx = await _load_base_context(uow, base_order_id)
    if ctx is None:
        return None

    offers: list[EligibleOffer] = []
    for addon in await _addon_catalog_items(uow, ctx):
        if addon.available_units <= 0:
            continue
        existing = await uow.addon_purchases.get(base_order_id, addon.sku)
        if existing is not None and existing.status != STATUS_OFFERED:
            continue
        offer = _to_offer(addon, nights=ctx.nights, rooms=ctx.rooms)
        if offer is not None:
            offers.append(offer)

    if offers:
        for offer in offers:
            await uow.addon_purchases.record_offered(
                id=new_id("adp"),
                base_order_id=base_order_id,
                offer_sku=offer.sku,
                price_minor=offer.total_minor,
                currency=offer.currency,
            )
        await uow.commit()

    return "INR", offers


async def price_offer_for_purchase(
    uow: UnitOfWork, *, base_order_id: str, offer_sku: str
) -> EligibleOffer | None:
    """Re-derives pricing fresh (never trusts a price the browser sends,
    never reuses a possibly-stale number from an earlier `list_eligible_
    offers` call) -- used immediately before building the addon mandate,
    so the mandate's own cap is always computed from the same live
    catalog read `create_quote` will pin moments later. Returns None if
    the offer/base order is no longer purchasable at all (out of stock,
    base order context gone, or not a recognised add-on)."""
    ctx = await _load_base_context(uow, base_order_id)
    if ctx is None:
        return None
    addon = await uow.catalog.get_item(offer_sku)
    if addon is None or not addon.category.startswith(_ADDON_CATEGORY_PREFIX):
        return None
    if addon.available_units <= 0:
        return None
    return _to_offer(addon, nights=ctx.nights, rooms=ctx.rooms)
