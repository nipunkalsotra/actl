"""§14 / §28 P7: POST /agent/v1/messages -- the single agent-to-agent
protocol dispatch endpoint. Every request routes through envelope
verification (protocol/algorithm, identity, signature, replay, timestamp
skew) before any business handling, exactly matching the seven message
types' typed request/response contracts (§28 P7 instruction 4). A
security/protocol-layer rejection never reaches business handling and is
never signed (there is no verified identity yet to address a response
to); every business-layer outcome is a full, signed response envelope.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application.agents import merchant
from actl.application.agents.envelope_service import EnvelopeRejected, verify_envelope
from actl.application.agents.merchant import HandlerError, HandlerOutcome
from actl.application.ports import PaymentProvider
from actl.domain.agent.envelope import REQUEST_MESSAGE_TYPES, AgentEnvelope
from actl.domain.policy.reason_codes import ReasonCode
from actl.infrastructure.cache.nonce import NonceCache
from actl.infrastructure.db.uow import UnitOfWork
from actl.interfaces.agent.schemas import (
    CapabilityDiscoverBody,
    CatalogQueryBody,
    OrderProposeBody,
    OrderStatusBody,
    QuoteRequestBody,
    ReceiptIssueBody,
)
from actl.interfaces.http.deps import (
    get_breaker,
    get_clock,
    get_nonce_cache,
    get_payment_provider,
    get_session_factory,
)
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import Clock

router = APIRouter()

# Security/protocol-layer rejections only -- business-layer outcomes are
# always HTTP 200 (a typed "error" or "order.propose" reject *is* a
# well-formed protocol response, per §14's own message table).
_REJECTION_STATUS: dict[ReasonCode, int] = {
    ReasonCode.MALFORMED_REQUEST: 400,
    ReasonCode.UNKNOWN_PROTOCOL_VERSION: 400,
    ReasonCode.UNKNOWN_ALGORITHM: 400,
    ReasonCode.IDENTITY_UNKNOWN: 401,
    ReasonCode.SIGNATURE_INVALID: 401,
    ReasonCode.IDENTITY_REVOKED: 403,
    ReasonCode.IDENTITY_EXPIRED: 403,
    ReasonCode.REPLAYED_MESSAGE: 409,
    ReasonCode.CLOCK_SKEW: 400,
    ReasonCode.REPLAY_CHECK_UNAVAILABLE: 503,
}


def _plain_error(reason_code: ReasonCode, message: str, *, retryable: bool = False) -> JSONResponse:
    return JSONResponse(
        content={"reason_code": str(reason_code), "message": message, "retryable": retryable},
        status_code=_REJECTION_STATUS.get(reason_code, 400),
    )


def _split_location(location: str | None) -> tuple[str | None, str | None]:
    if not location:
        return None, None
    city, _, country = location.partition(",")
    return (city.strip() or None), (country.strip() or None)


async def _dispatch(
    envelope: AgentEnvelope,
    session_factory: async_sessionmaker[AsyncSession],
    clock: Clock,
    provider: PaymentProvider,
    breaker: CircuitBreaker,
) -> HandlerOutcome:
    actor_id = envelope.from_

    if envelope.type == "capability.discover":
        try:
            CapabilityDiscoverBody.model_validate(envelope.body)
        except ValidationError:
            return HandlerError(ReasonCode.MALFORMED_REQUEST, "invalid capability.discover body")
        return await merchant.handle_capability_discover()

    if envelope.type == "catalog.query":
        try:
            body = CatalogQueryBody.model_validate(envelope.body)
        except ValidationError:
            return HandlerError(ReasonCode.MALFORMED_REQUEST, "invalid catalog.query body")
        city, country = _split_location(body.location)
        async with UnitOfWork(session_factory) as uow:
            return await merchant.handle_catalog_query(
                uow,
                clock,
                category=body.category,
                location_city=city,
                location_country=country,
                max_unit_minor=body.max_unit_minor,
                cursor=body.cursor,
                limit=body.limit,
                actor_id=actor_id,
            )

    if envelope.type == "quote.request":
        try:
            qbody = QuoteRequestBody.model_validate(envelope.body)
        except ValidationError:
            return HandlerError(ReasonCode.MALFORMED_REQUEST, "invalid quote.request body")
        async with UnitOfWork(session_factory) as uow:
            return await merchant.handle_quote_request(
                uow,
                clock,
                sku=qbody.sku,
                mandate_id=qbody.mandate_id,
                nights=qbody.nights,
                actor_id=actor_id,
            )

    if envelope.type == "order.propose":
        try:
            obody = OrderProposeBody.model_validate(envelope.body)
        except ValidationError:
            return HandlerError(ReasonCode.MALFORMED_REQUEST, "invalid order.propose body")
        return await merchant.handle_order_propose(
            session_factory,
            provider,
            clock,
            breaker,
            quote_id=obody.quote_id,
            quote_hash=obody.quote_hash,
            mandate_id=obody.mandate_id,
            mandate_spec_hash=obody.mandate_spec_hash,
            intent_hash=obody.intent_hash,
            trace_id=envelope.corr_id,
            actor_id=actor_id,
        )

    if envelope.type == "order.status":
        try:
            sbody = OrderStatusBody.model_validate(envelope.body)
        except ValidationError:
            return HandlerError(ReasonCode.MALFORMED_REQUEST, "invalid order.status body")
        async with UnitOfWork(session_factory) as uow:
            return await merchant.handle_order_status(uow, order_id=sbody.order_id)

    if envelope.type == "receipt.issue":
        try:
            rbody = ReceiptIssueBody.model_validate(envelope.body)
        except ValidationError:
            return HandlerError(ReasonCode.MALFORMED_REQUEST, "invalid receipt.issue body")
        async with UnitOfWork(session_factory) as uow:
            return await merchant.handle_receipt_issue(uow, order_id=rbody.order_id)

    return HandlerError(ReasonCode.MALFORMED_REQUEST, f"unhandled message type {envelope.type!r}")


@router.post("/agent/v1/messages")
async def post_message(
    raw: dict[str, Any] = Body(...),
    clock: Clock = Depends(get_clock),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    nonce_cache: NonceCache = Depends(get_nonce_cache),
    provider: PaymentProvider = Depends(get_payment_provider),
    breaker: CircuitBreaker = Depends(get_breaker),
) -> JSONResponse:
    try:
        envelope = AgentEnvelope.model_validate(raw)
    except ValidationError as exc:
        return _plain_error(
            ReasonCode.MALFORMED_REQUEST, f"malformed envelope: {exc.error_count()} error(s)"
        )

    async with UnitOfWork(session_factory) as uow:
        result = await verify_envelope(uow, nonce_cache, clock, envelope)
    if isinstance(result, EnvelopeRejected):
        return _plain_error(result.reason_code, result.message, retryable=result.retryable)

    if envelope.type not in REQUEST_MESSAGE_TYPES:
        outcome: HandlerOutcome = HandlerError(
            ReasonCode.MALFORMED_REQUEST, f"{envelope.type!r} is not a request type"
        )
    else:
        outcome = await _dispatch(envelope, session_factory, clock, provider, breaker)

    response = merchant.build_response_envelope(envelope, outcome, clock)
    return JSONResponse(content=response.model_dump(mode="json", by_alias=True), status_code=200)
