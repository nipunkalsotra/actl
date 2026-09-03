"""§13.1 GET /agent/v1/catalog, §13.2 POST /agent/v1/quote.

Auth per Appendix A is "Signed envelope" -- that's §14's agent-to-agent
protocol layer, built in P7 on top of what this router exposes. §28 P4's
own deliverables list and CLAUDE CODE PROMPT do not ask for envelope
verification here, so these routes are plain typed REST for now; see
docs/adr/0005-p4-catalog-quote-decisions.md.
"""

from __future__ import annotations

import hashlib
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from actl.application.catalog_service import (
    CatalogQuery,
    MandateNotFound,
    SkuNotFound,
    SkuUnavailable,
    create_quote,
    list_catalog,
)
from actl.domain.audit.canonical import jcs
from actl.domain.catalog.models import MAX_UNIT_PRICE_MINOR
from actl.infrastructure.db.uow import UnitOfWork
from actl.interfaces.http.deps import get_clock, get_uow
from actl.platform.clock import Clock

router = APIRouter()

_CACHE_CONTROL = "max-age=30"

# Postgres/asyncpg cannot represent a NUL byte in a text value at all
# (`CharacterNotInRepertoireError`) -- these fields reach a SQL WHERE
# clause via CatalogQuery, so reject one here rather than crash with a 500.
_NO_NUL_BYTES = r"^[^\x00]*$"


def _parse_location(location: str | None) -> tuple[str | None, str | None]:
    if location is None:
        return None, None
    city, _, country = location.partition(",")
    return (city or None), (country or None)


def _compute_etag(catalog_version: int, query: CatalogQuery) -> str:
    """A strong ETag derived from catalog_version (§13.1) plus a short hash
    of the query itself -- two different filters at the same catalog
    version must not collide on one ETag, or If-None-Match against one view
    could wrongly 304 a request for a different one."""
    filter_payload: dict[str, Any] = {
        "category": query.category,
        "location_city": query.location_city,
        "location_country": query.location_country,
        "max_unit_minor": query.max_unit_minor,
        "cursor": query.cursor,
        "limit": query.limit,
    }
    filter_hash = hashlib.sha256(jcs(filter_payload).encode("utf-8")).hexdigest()[:4]
    return f'"cat-v{catalog_version}-{filter_hash}"'


def _if_none_match_hits(header_value: str, etag: str) -> bool:
    candidates = [c.strip() for c in header_value.split(",")]
    return "*" in candidates or etag in candidates


@router.get("/agent/v1/catalog")
async def get_catalog(
    request: Request,
    category: Annotated[str | None, Query(pattern=_NO_NUL_BYTES)] = None,
    location: Annotated[str | None, Query(pattern=_NO_NUL_BYTES)] = None,
    max_unit_minor: Annotated[int | None, Query(gt=0, le=MAX_UNIT_PRICE_MINOR)] = None,
    cursor: Annotated[str | None, Query(pattern=_NO_NUL_BYTES)] = None,
    limit: Annotated[int, Query(gt=0, le=100)] = 20,
    uow: UnitOfWork = Depends(get_uow),
    clock: Clock = Depends(get_clock),
) -> Response:
    location_city, location_country = _parse_location(location)
    query = CatalogQuery(
        category=category,
        location_city=location_city,
        location_country=location_country,
        max_unit_minor=max_unit_minor,
        cursor=cursor,
        limit=limit,
    )

    version = await uow.catalog.current_version()
    etag = _compute_etag(version, query)
    headers = {"ETag": etag, "Cache-Control": _CACHE_CONTROL}

    if_none_match = request.headers.get("if-none-match")
    if if_none_match and _if_none_match_hits(if_none_match, etag):
        return Response(status_code=304, headers=headers)

    actor_id = request.headers.get("x-agent-id", "agt_unknown")
    try:
        feed = await list_catalog(uow, clock, query, actor_id=actor_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return JSONResponse(content=feed.model_dump(mode="json", by_alias=True), headers=headers)


class QuoteRequest(BaseModel):
    sku: str = Field(min_length=1, pattern=_NO_NUL_BYTES)
    mandate_id: str = Field(min_length=1, pattern=_NO_NUL_BYTES)
    nights: int = Field(gt=0)


@router.post("/agent/v1/quote", status_code=201)
async def post_quote(
    body: QuoteRequest,
    request: Request,
    uow: UnitOfWork = Depends(get_uow),
    clock: Clock = Depends(get_clock),
) -> dict[str, Any]:
    actor_id = request.headers.get("x-agent-id", "agt_unknown")
    try:
        quote = await create_quote(
            uow,
            clock,
            mandate_id=body.mandate_id,
            sku=body.sku,
            nights=body.nights,
            actor_id=actor_id,
        )
    except MandateNotFound as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    except SkuNotFound as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    except SkuUnavailable as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc

    return quote.model_dump(mode="json", by_alias=True)
