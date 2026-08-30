"""§22 / Appendix A: GET /audit/explain/{order_id} -- "Read token" auth,
a separate tier from the admin token (`interfaces/http/routers/admin.py`)
so a reviewer/dashboard credential can never also mutate the catalog.
"""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from actl.application.explain_service import OrderNotFoundForExplain, explain_order
from actl.config import settings
from actl.infrastructure.db.uow import UnitOfWork
from actl.interfaces.http.deps import get_uow

router = APIRouter(tags=["audit"])


def _require_read_token(request: Request) -> None:
    provided = request.headers.get("authorization", "")
    prefix = "Bearer "
    token = provided[len(prefix) :] if provided.startswith(prefix) else ""
    if not hmac.compare_digest(token, settings.read_token):
        raise HTTPException(status_code=401, detail="invalid or missing read token")


@router.get("/audit/explain/{order_id}")
async def explain(
    order_id: str, request: Request, uow: UnitOfWork = Depends(get_uow)
) -> dict[str, Any]:
    """§22: the ordered causal timeline for one order, with hashes --
    never a secret, private key, raw webhook signature/body, or sensitive
    internal configuration value (only `application.explain_service`'s
    own deliberately-narrow payload projections ever reach this
    response)."""
    _require_read_token(request)
    try:
        result = await explain_order(uow, order_id)
    except OrderNotFoundForExplain:
        raise HTTPException(
            status_code=404, detail={"reason_code": "ORDER_NOT_FOUND", "order_id": order_id}
        ) from None

    return {
        "order_id": result.order_id,
        "terminal_outcome": {"status": result.terminal_status},
        "timeline": [
            {
                "seq": item.seq,
                "ts": item.ts.isoformat() if item.ts is not None else None,
                "type": item.type,
                "action": item.action,
                "trace_id": item.trace_id,
                "hashes": {
                    "entry_hash": item.entry_hash,
                    "prev_hash": item.prev_hash,
                    "payload_hash": item.payload_hash,
                },
                "payload": item.payload,
            }
            for item in result.timeline
        ],
    }
