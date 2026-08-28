"""§15.3 POST /webhooks/razorpay -- verify, dedupe, return fast. Processing
(the actual order state transition) happens on the worker
(`application.payment_service.process_unprocessed_webhooks`), never in
this handler -- "a slow handler causes provider retries and a
self-inflicted thundering herd."

Security correction (post-P5 review): the constant-time signature check
in `process_webhook_delivery` runs before any database call, so a
missing/malformed/invalid `X-Razorpay-Signature` is rejected with 401 and
leaves nothing persisted -- no `webhook_events` row, no outbox row, no
state transition, no worker work. Only once that check passes does a
delivery reach durable, idempotent storage. If the durable handoff itself
fails (a real database error after a valid signature), the exception
propagates and FastAPI's default handler returns 500 -- a non-2xx, so
Razorpay's own retry policy covers it; nothing here manufactures a false
200 for a delivery that was never actually persisted.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from actl.application.payment_service import process_webhook_delivery
from actl.application.ports import PaymentProvider
from actl.infrastructure.db.uow import UnitOfWork
from actl.interfaces.http.deps import get_payment_provider, get_uow

router = APIRouter()


@router.post("/webhooks/razorpay")
async def razorpay_webhook(
    request: Request,
    uow: UnitOfWork = Depends(get_uow),
    provider: PaymentProvider = Depends(get_payment_provider),
) -> Response:
    raw_body = await request.body()
    signature = request.headers.get("x-razorpay-signature", "")
    event_id = request.headers.get("x-razorpay-event-id", "")

    try:
        parsed: Any = json.loads(raw_body)
    except ValueError:
        parsed = {}
    body: dict[str, object] = parsed if isinstance(parsed, dict) else {}
    event_type = str(body.get("event", "unknown"))

    receipt = await process_webhook_delivery(
        uow,
        provider,
        raw_body=raw_body,
        signature=signature,
        event_id=event_id,
        event_type=event_type,
        payload=body,
    )

    if receipt.outcome == "invalid_signature":
        # Never reveal the expected signature or the webhook secret --
        # a bare 401 with no body.
        return Response(status_code=401)

    # "accepted", "duplicate", or "missing_event_id" (a valid signature we
    # still can't safely dedupe) -- all a fast 2xx with nothing further to
    # report; the worker owns processing from here.
    return Response(status_code=200)
