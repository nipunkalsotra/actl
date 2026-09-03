"""Catalog v1 (§13.1): a projection for a machine, not the human catalog
with images removed. Every commercially relevant fact is a typed field —
"the feed contains no free-text description field at all" is a security
decision (§21.3 prompt injection through merchant copy), not a display
choice, so no field on any model below holds unstructured prose.

Money is always `unit_price_minor`, an integer in minor units, matching
§8.1's mandate money fields (StrictInt so a float is rejected at
construction, never silently coerced).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

CURRENCY: Literal["INR"] = "INR"

# unit_price_minor (and any query bound on it, e.g. max_unit_minor) is
# stored as a Postgres BigInteger (int64) column -- a value above this
# overflows the DB bind deep in the repository (an unhandled asyncpg
# DataError) instead of failing typed validation at the boundary, where
# every interface that accepts this field must reject it instead.
MAX_UNIT_PRICE_MINOR = 9223372036854775807


class CatalogLocation(BaseModel):
    model_config = ConfigDict(frozen=True)

    city: str
    country: str


class CatalogAttributes(BaseModel):
    """Typed, ranking/filtering-relevant facts (§13.1). Fixed shape: this
    build's catalog is travel.hotel only, so the attribute set is the exact
    three fields the doc's own §13.1 example shows — extend when a second
    category is actually added, not speculatively now."""

    model_config = ConfigDict(frozen=True)

    rating: float
    sea_facing: bool
    breakfast_included: bool


class CatalogPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    refundable: bool
    cancellation_window_h: int
    instant_confirm: bool
    taxes_included: bool


class CatalogItem(BaseModel):
    """One §13.1 feed item. No description/summary/notes field — enforced
    by this being the complete field list, not by a blocklist."""

    model_config = ConfigDict(frozen=True)

    sku: str
    category: str
    merchant_id: str
    unit: str
    unit_price_minor: StrictInt
    available_units: int
    location: CatalogLocation
    attributes: CatalogAttributes
    policy: CatalogPolicy
    version: int
    quote_required: bool


class CatalogFeed(BaseModel):
    """§13.1 top-level response envelope for GET /agent/v1/catalog."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    schema_: Literal["actl.catalog/v1"] = Field(alias="schema", default="actl.catalog/v1")
    catalog_version: int
    generated_at: datetime
    currency: Literal["INR"] = CURRENCY
    items: list[CatalogItem]
    next_cursor: str | None = None
