"""§14 request body schemas -- one per message type, validated after
envelope verification but before business handling. Kept separate from
`application.agents.merchant`'s handler signatures so the HTTP/Pydantic
concern stays in `interfaces`, matching `interfaces.http.routers.catalog`'s
existing `QuoteRequest` pattern.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# Postgres/asyncpg cannot represent a NUL byte in a text value at all
# (`CharacterNotInRepertoireError`) -- any of these fields can reach a SQL
# WHERE clause downstream, so reject one here rather than crash deep in a
# repository with a 500. Same constraint as domain.agent.envelope's.
_NO_NUL_BYTES = r"^[^\x00]*$"


class CapabilityDiscoverBody(BaseModel):
    supported_protocols: list[str] = Field(min_length=1)


class CatalogQueryBody(BaseModel):
    category: str | None = Field(default=None, pattern=_NO_NUL_BYTES)
    location: str | None = Field(default=None, pattern=_NO_NUL_BYTES)
    max_unit_minor: int | None = Field(default=None, gt=0)
    cursor: str | None = Field(default=None, pattern=_NO_NUL_BYTES)
    limit: int = Field(default=20, gt=0, le=100)


class QuoteRequestBody(BaseModel):
    sku: str = Field(min_length=1, pattern=_NO_NUL_BYTES)
    mandate_id: str = Field(min_length=1, pattern=_NO_NUL_BYTES)
    nights: int = Field(gt=0)


class OrderProposeBody(BaseModel):
    quote_id: str = Field(min_length=1, pattern=_NO_NUL_BYTES)
    quote_hash: str = Field(min_length=1, pattern=_NO_NUL_BYTES)
    mandate_id: str = Field(min_length=1, pattern=_NO_NUL_BYTES)
    mandate_spec_hash: str = Field(min_length=1, pattern=_NO_NUL_BYTES)
    intent_hash: str = Field(min_length=1, pattern=_NO_NUL_BYTES)


class OrderStatusBody(BaseModel):
    order_id: str = Field(min_length=1, pattern=_NO_NUL_BYTES)


class ReceiptIssueBody(BaseModel):
    order_id: str = Field(min_length=1, pattern=_NO_NUL_BYTES)
