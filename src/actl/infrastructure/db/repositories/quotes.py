"""Quote repository (§18.2 `quotes` — not shown in the excerpt, added per
ADR 0003; columns are the Quote v1 fields from §8.4).

No P1 domain model exists for a quote yet — `catalog/quote.py` is P4's
deliverable (§25, §6.2: "catalog: ... quotes and price locks"). `QuoteRecord`
is a local, infrastructure-only record so this repository can exist now
without inventing P4's domain model early; redirect it to
`actl.domain.catalog.Quote` once that phase adds it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from actl.infrastructure.db.models import QuoteRow


@dataclass(frozen=True)
class QuoteRecord:
    id: str
    mandate_id: str
    sku: str
    unit_price_minor: int
    nights: int
    total_minor: int
    currency: str
    catalog_version: int
    refundable: bool
    quote_token: str
    quote_hash: str
    expires_at: datetime


class QuoteRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, quote: QuoteRecord) -> None:
        row = QuoteRow(
            id=quote.id,
            mandate_id=quote.mandate_id,
            sku=quote.sku,
            unit_price_minor=quote.unit_price_minor,
            nights=quote.nights,
            total_minor=quote.total_minor,
            currency=quote.currency,
            catalog_version=quote.catalog_version,
            refundable=quote.refundable,
            quote_token=quote.quote_token,
            quote_hash=quote.quote_hash,
            expires_at=quote.expires_at,
        )
        self._session.add(row)

    async def get(self, quote_id: str) -> QuoteRecord | None:
        row = await self._session.get(QuoteRow, quote_id)
        if row is None:
            return None
        return QuoteRecord(
            id=row.id,
            mandate_id=row.mandate_id,
            sku=row.sku,
            unit_price_minor=row.unit_price_minor,
            nights=row.nights,
            total_minor=row.total_minor,
            currency=row.currency,
            catalog_version=row.catalog_version,
            refundable=row.refundable,
            quote_token=row.quote_token,
            quote_hash=row.quote_hash,
            expires_at=row.expires_at,
        )
