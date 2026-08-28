"""Mandate v1 (§8.1): authority as a signed, hashed, expiring data structure.

Every amount is an integer in minor units (paise) — `StrictInt` on every
money field so a float is rejected at construction, not silently coerced.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt


class Principal(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: str
    id: str


class Delegate(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: str
    id: str
    key_id: str


class MandateIntent(BaseModel):
    model_config = ConfigDict(frozen=True)

    category: str
    location: str
    check_in: str
    nights: int
    rooms: int


class MandateBounds(BaseModel):
    model_config = ConfigDict(frozen=True)

    currency: str
    max_total_minor: StrictInt
    max_unit_minor: StrictInt
    max_transactions: int
    allowed_categories: list[str]
    blocked_merchants: list[str]
    require_refundable: bool
    max_price_delta_bps: StrictInt


class MandateTemporal(BaseModel):
    model_config = ConfigDict(frozen=True)

    not_before: datetime
    expires_at: datetime
    quote_ttl_s: int


class MandateControls(BaseModel):
    model_config = ConfigDict(frozen=True)

    human_confirm_required: bool
    revocable: bool


class MandateSignature(BaseModel):
    model_config = ConfigDict(frozen=True)

    alg: str
    key_id: str
    value: str


class Mandate(BaseModel):
    """§8.1. `spec_hash`/`signature` are None until the PENDING_CONFIRM ->
    LOCKED transition (§9.1) computes and attaches them — one model covers
    the whole lifecycle rather than a separate draft/locked pair of types."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    schema_: Literal["actl.mandate/v1"] = Field(alias="schema", default="actl.mandate/v1")
    mandate_id: str
    version: int
    principal: Principal
    delegate: Delegate
    intent: MandateIntent
    bounds: MandateBounds
    temporal: MandateTemporal
    controls: MandateControls
    spec_hash: str | None = None
    signature: MandateSignature | None = None
