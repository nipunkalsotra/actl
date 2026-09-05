"""§13: catalog reads and quote issuance. Every write here goes through the
same P3 audit_service.append_entry() the rest of the system uses — the
admin price mutation is a real state change and §28 P4 explicitly requires
it "never bypass audit" (see docs/adr/0005-p4-catalog-quote-decisions.md).

Full mandate-status/budget validation (does the mandate allow this
purchase, is it LOCKED, does the price fit its bounds) is P6's Money Action
Gate, not this phase — create_quote here validates only that the mandate
and sku exist and the item is in stock, matching §28 P4's explicit scope
("do not perform payment, order creation, capture").
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from actl.application.audit_service import append_entry
from actl.config import settings
from actl.domain.audit.events import AuditAction
from actl.domain.catalog.models import (
    CatalogAttributes,
    CatalogFeed,
    CatalogItem,
    CatalogLocation,
    CatalogPolicy,
)
from actl.domain.catalog.quote import Quote, build_quote_token, compute_quote_hash
from actl.infrastructure.db.repositories.catalog import CatalogItemRecord
from actl.infrastructure.db.repositories.quotes import QuoteRecord
from actl.infrastructure.db.uow import UnitOfWork
from actl.platform import tracing
from actl.platform.clock import Clock
from actl.platform.errors import ActlError
from actl.platform.ids import new_id


class SkuNotFound(ActlError):
    reason_code = "SKU_NOT_FOUND"


class SkuUnavailable(ActlError):
    reason_code = "SKU_UNAVAILABLE"


class MandateNotFound(ActlError):
    reason_code = "MANDATE_NOT_FOUND"


class InvalidPriceMutation(ActlError):
    reason_code = "INVALID_PRICE"


def _to_domain_item(record: CatalogItemRecord) -> CatalogItem:
    return CatalogItem(
        sku=record.sku,
        category=record.category,
        merchant_id=record.merchant_id,
        unit=record.unit,
        unit_price_minor=record.unit_price_minor,
        available_units=record.available_units,
        location=CatalogLocation(city=record.location_city, country=record.location_country),
        attributes=CatalogAttributes(
            rating=record.rating,
            sea_facing=record.sea_facing,
            breakfast_included=record.breakfast_included,
        ),
        policy=CatalogPolicy(
            refundable=record.refundable,
            cancellation_window_h=record.cancellation_window_h,
            instant_confirm=record.instant_confirm,
            taxes_included=record.taxes_included,
        ),
        version=record.version,
        quote_required=record.quote_required,
    )


def _encode_cursor(unit_price_minor: int, sku: str) -> str:
    return f"{unit_price_minor}:{sku}"


def _decode_cursor(cursor: str) -> tuple[int, str]:
    price_str, _, sku = cursor.partition(":")
    if not price_str.isdigit() or not sku:
        raise ValueError(f"malformed cursor: {cursor!r}")
    return int(price_str), sku


@dataclass(frozen=True)
class CatalogQuery:
    category: str | None = None
    location_city: str | None = None
    location_country: str | None = None
    max_unit_minor: int | None = None
    # None (the §14 agent protocol and admin routes): no filter, every real
    # row visible. True (interfaces.http.routers.buyer only): excludes
    # Trust Lab / growth-simulation rows from the buyer-facing grid.
    is_buyer_listable: bool | None = None
    cursor: str | None = None
    limit: int = 20


async def list_catalog(
    uow: UnitOfWork,
    clock: Clock,
    query: CatalogQuery,
    *,
    actor_id: str = "agt_unknown",
) -> CatalogFeed:
    """§13.1. Writes a catalog.queried audit entry (§16.3) with the filters
    actually applied and the result count."""
    trace_id = new_id("trc")
    with tracing.transaction_span("catalog.list_catalog", trace_id, actor_id=actor_id):
        decoded_cursor = _decode_cursor(query.cursor) if query.cursor else None
        version = await uow.catalog.current_version()

        rows = await uow.catalog.list_items(
            category=query.category,
            location_city=query.location_city,
            location_country=query.location_country,
            max_unit_minor=query.max_unit_minor,
            is_buyer_listable=query.is_buyer_listable,
            cursor=decoded_cursor,
            limit=query.limit + 1,
        )
        has_more = len(rows) > query.limit
        page = rows[: query.limit]
        next_cursor = (
            _encode_cursor(page[-1].unit_price_minor, page[-1].sku) if has_more and page else None
        )

        feed = CatalogFeed(
            catalog_version=version,
            generated_at=clock.now(),
            items=[_to_domain_item(row) for row in page],
            next_cursor=next_cursor,
        )

        await append_entry(
            uow,
            trace_id=trace_id,
            actor_type="agent",
            actor_id=actor_id,
            action=AuditAction.CATALOG_QUERIED,
            subject={"category": query.category, "location": query.location_city},
            payload={
                "filters": {
                    "category": query.category,
                    "location_city": query.location_city,
                    "location_country": query.location_country,
                    "max_unit_minor": query.max_unit_minor,
                    "is_buyer_listable": query.is_buyer_listable,
                },
                "catalog_version": version,
                "result_count": len(page),
            },
        )
        await uow.commit()
        return feed


async def create_quote(
    uow: UnitOfWork,
    clock: Clock,
    *,
    mandate_id: str,
    sku: str,
    nights: int,
    actor_id: str = "agt_unknown",
) -> Quote:
    """§13.2 / §8.4. Pins unit_price_minor and catalog_version at this
    instant, sets expires_at from QUOTE_TTL_S, signs quote_token, persists
    through the P2 quotes repository, and writes a quote.issued audit
    entry -- all in one UnitOfWork transaction."""
    trace_id = new_id("trc")
    with tracing.transaction_span("catalog.create_quote", trace_id, mandate_id=mandate_id, sku=sku):
        mandate = await uow.mandates.get(mandate_id)
        if mandate is None:
            raise MandateNotFound(f"no mandate {mandate_id}", details={"mandate_id": mandate_id})

        item = await uow.catalog.get_item(sku)
        if item is None:
            raise SkuNotFound(f"no catalog item {sku}", details={"sku": sku})
        if item.available_units <= 0:
            raise SkuUnavailable(f"{sku} has no available units", details={"sku": sku})

        # Pin the *global* epoch, not this item's own last-mutated marker --
        # G5 (gate.py) compares against uow.catalog.current_version(), which
        # advances on ANY item's mutation. An item that's never been
        # individually mutated keeps item.version frozen at its seed value
        # forever, so pinning that would make it spuriously, permanently
        # STALE_PRICE the instant any *other* item is ever mutated.
        current_version = await uow.catalog.current_version()

        total_minor = item.unit_price_minor * nights
        draft = Quote(
            quote_id=new_id("qte"),
            sku=item.sku,
            mandate_id=mandate_id,
            unit_price_minor=item.unit_price_minor,
            nights=nights,
            total_minor=total_minor,
            catalog_version=current_version,
            refundable=item.refundable,
            expires_at=clock.now() + timedelta(seconds=settings.quote_ttl_s),
        )
        quote_hash = compute_quote_hash(draft)
        quote_token = build_quote_token(
            draft, quote_hash, settings.quote_signing_key.encode("utf-8")
        )
        quote = draft.model_copy(update={"quote_hash": quote_hash, "quote_token": quote_token})

        await uow.quotes.add(
            QuoteRecord(
                id=quote.quote_id,
                mandate_id=quote.mandate_id,
                sku=quote.sku,
                unit_price_minor=quote.unit_price_minor,
                nights=quote.nights,
                total_minor=quote.total_minor,
                currency=quote.currency,
                catalog_version=quote.catalog_version,
                refundable=quote.refundable,
                quote_token=quote.quote_token or "",
                quote_hash=quote.quote_hash or "",
                expires_at=quote.expires_at,
            )
        )

        await append_entry(
            uow,
            trace_id=trace_id,
            actor_type="agent",
            actor_id=actor_id,
            action=AuditAction.QUOTE_ISSUED,
            subject={"sku": quote.sku, "quote_id": quote.quote_id},
            payload={
                "sku": quote.sku,
                "price_minor": quote.unit_price_minor,
                "catalog_version": quote.catalog_version,
                "expires_at": quote.expires_at.isoformat(),
            },
        )
        await uow.commit()
        return quote


async def mutate_price_demo_only(
    uow: UnitOfWork,
    *,
    sku: str,
    new_unit_price_minor: int,
    actor_id: str,
) -> CatalogItem:
    """§28 P4: demo-only endpoint used to trigger the stale-price scenario.
    Never call this from anything but the admin router — it exists purely
    to move a price out from under an in-flight quote."""
    trace_id = new_id("trc")
    with tracing.transaction_span("catalog.mutate_price_demo_only", trace_id, sku=sku):
        if new_unit_price_minor <= 0:
            raise InvalidPriceMutation("unit_price_minor must be positive")

        try:
            updated = await uow.catalog.mutate_price(sku, new_unit_price_minor)
        except KeyError as exc:
            raise SkuNotFound(f"no catalog item {sku}", details={"sku": sku}) from exc

        await append_entry(
            uow,
            trace_id=trace_id,
            actor_type="admin",
            actor_id=actor_id,
            action=AuditAction.CATALOG_PRICE_MUTATED,
            subject={"sku": sku},
            payload={
                "sku": sku,
                "new_unit_price_minor": new_unit_price_minor,
                "catalog_version": updated.version,
            },
        )
        await uow.commit()
        return _to_domain_item(updated)
