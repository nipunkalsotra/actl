"""§28 P4: POST /admin/catalog/{sku}/price -- demo-only, used only to
trigger the stale-price scenario (Appendix A: Auth "Admin token"). Never a
path real traffic hits; label it as such in both the route tag and the
response body so it can't be mistaken for a production surface.
"""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from actl.application.catalog_service import (
    InvalidPriceMutation,
    SkuNotFound,
    mutate_price_demo_only,
)
from actl.config import settings
from actl.infrastructure.db.uow import UnitOfWork
from actl.interfaces.http.deps import get_uow

router = APIRouter(tags=["demo-only-admin"])


class PriceMutationRequest(BaseModel):
    unit_price_minor: int = Field(gt=0)


def _require_admin_token(request: Request) -> None:
    provided = request.headers.get("authorization", "")
    prefix = "Bearer "
    token = provided[len(prefix) :] if provided.startswith(prefix) else ""
    if not hmac.compare_digest(token, settings.admin_token):
        raise HTTPException(status_code=401, detail="invalid or missing admin token")


@router.post("/admin/catalog/{sku}/price")
async def mutate_price(
    sku: str,
    body: PriceMutationRequest,
    request: Request,
    uow: UnitOfWork = Depends(get_uow),
) -> dict[str, Any]:
    """DEMO-ONLY: mutates a catalog item's price out from under any
    in-flight quote so the stale-price scenario is reproducible on demand.
    Never wired to real merchant catalog management."""
    _require_admin_token(request)
    actor_id = request.headers.get("x-admin-id", "admin_demo")
    try:
        item = await mutate_price_demo_only(
            uow, sku=sku, new_unit_price_minor=body.unit_price_minor, actor_id=actor_id
        )
    except SkuNotFound as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    except InvalidPriceMutation as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc

    return {
        "demo_only": True,
        "warning": "this endpoint exists only to trigger the stale-price demo scenario",
        "item": item.model_dump(mode="json", by_alias=True),
    }
