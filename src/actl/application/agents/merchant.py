"""§14 merchant-agent: the six request-handling message types
(capability.discover, catalog.query, quote.request, order.propose,
order.status, receipt.issue). "error" is a response-only shape,
constructed by the caller from a `HandlerError`, not handled here.

§28 P7 instruction 5's security boundary lives entirely in
`handle_order_propose`: it loads the mandate from this process's own
database by `mandate_id` and compares the stored `mandate_spec_hash`
against what the buyer claims -- it never parses, persists, or trusts a
buyer-supplied mandate body (there is no such body in the wire protocol
at all; §8.4's own WHY THIS WAY note is why). A buyer that lies about the
hash, the intent_hash, or any bound is caught here, before the policy
engine or the gate ever sees the request.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application import ledger_service
from actl.application.audit_service import append_entry
from actl.application.catalog_service import (
    CatalogQuery,
    MandateNotFound,
    SkuNotFound,
    SkuUnavailable,
    create_quote,
    list_catalog,
)
from actl.application.gate import MoneyActionRequest
from actl.application.orchestrator.saga import begin_purchase
from actl.application.ports import PaymentProvider
from actl.config import settings
from actl.domain.agent.envelope import (
    PROTOCOL_ID,
    AgentEnvelope,
    MessageType,
    sign_envelope_ed25519,
)
from actl.domain.audit.events import AuditAction
from actl.domain.mandate.state_machine import MandateStatus
from actl.domain.policy.engine import evaluate
from actl.domain.policy.reason_codes import ReasonCode
from actl.domain.policy.rules import PolicyContext, PurchaseIntent, compute_intent_hash
from actl.infrastructure.db.uow import UnitOfWork
from actl.platform import metrics, tracing
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import Clock
from actl.platform.ids import new_id

CAPABILITY_DOCUMENT: dict[str, object] = {
    "protocol": "actl.acp/1",
    "currency": "INR",
    "endpoints": {
        "catalog": "/agent/v1/catalog",
        "quote": "/agent/v1/quote",
        "messages": "/agent/v1/messages",
    },
    "signing": {"algorithms": ["Ed25519", "HMAC-SHA256"]},
    "limits": {"quote_ttl_s": settings.quote_ttl_s},
}


@dataclass(frozen=True)
class HandlerResult:
    type: Literal[
        "capability.discover",
        "catalog.query",
        "quote.request",
        "order.propose",
        "order.status",
        "receipt.issue",
    ]
    body: dict[str, object]


@dataclass(frozen=True)
class HandlerError:
    reason_code: ReasonCode
    message: str
    retryable: bool = False


HandlerOutcome = HandlerResult | HandlerError


async def handle_capability_discover() -> HandlerResult:
    return HandlerResult(type="capability.discover", body=dict(CAPABILITY_DOCUMENT))


async def handle_catalog_query(
    uow: UnitOfWork,
    clock: Clock,
    *,
    category: str | None,
    location_city: str | None,
    location_country: str | None,
    max_unit_minor: int | None,
    cursor: str | None,
    limit: int,
    actor_id: str,
) -> HandlerResult:
    with tracing.span("use_case.catalog_query", actor_id=actor_id):
        feed = await list_catalog(
            uow,
            clock,
            CatalogQuery(
                category=category,
                location_city=location_city,
                location_country=location_country,
                max_unit_minor=max_unit_minor,
                cursor=cursor,
                limit=limit,
            ),
            actor_id=actor_id,
        )
        return HandlerResult(
            type="catalog.query", body=feed.model_dump(mode="json", by_alias=True)
        )


async def handle_quote_request(
    uow: UnitOfWork, clock: Clock, *, sku: str, mandate_id: str, nights: int, actor_id: str
) -> HandlerOutcome:
    with tracing.span("use_case.quote_request", mandate_id=mandate_id, sku=sku):
        try:
            quote = await create_quote(
                uow, clock, mandate_id=mandate_id, sku=sku, nights=nights, actor_id=actor_id
            )
        except MandateNotFound:
            return HandlerError(ReasonCode.MANDATE_INVALID, f"no mandate {mandate_id}")
        except SkuNotFound:
            return HandlerError(ReasonCode.STALE_PRICE, f"no catalog item {sku}")
        except SkuUnavailable:
            return HandlerError(ReasonCode.STALE_PRICE, f"{sku} has no available units")
        return HandlerResult(
            type="quote.request", body=quote.model_dump(mode="json", by_alias=True)
        )


async def handle_order_propose(
    session_factory: async_sessionmaker[AsyncSession],
    provider: PaymentProvider,
    clock: Clock,
    breaker: CircuitBreaker,
    *,
    quote_id: str,
    quote_hash: str,
    mandate_id: str,
    mandate_spec_hash: str,
    intent_hash: str,
    trace_id: str,
    actor_id: str,
) -> HandlerResult:
    """§28 P7 instruction 5. Every value used to build the `PurchaseIntent`
    and evaluate policy comes from this merchant's own database -- the
    quote (by `quote_id`) and the mandate (by `mandate_id`) -- never from
    the buyer's claimed `mandate_spec_hash`/`quote_hash`/`intent_hash`,
    which are only ever *compared against*, never trusted as data."""
    with tracing.transaction_span(
        "use_case.order_propose", trace_id, mandate_id=mandate_id, quote_id=quote_id
    ):
        async with UnitOfWork(session_factory) as uow:
            loaded = await uow.mandates.get(mandate_id)
            if loaded is None:
                return _reject(trace_id, ReasonCode.MANDATE_INVALID)
            mandate, status = loaded
            if status is MandateStatus.REVOKED:
                return _reject(trace_id, ReasonCode.MANDATE_REVOKED)
            if status not in (MandateStatus.LOCKED, MandateStatus.EXECUTING):
                return _reject(trace_id, ReasonCode.MANDATE_INVALID)
            if clock.now() >= mandate.temporal.expires_at:
                return _reject(trace_id, ReasonCode.MANDATE_EXPIRED)
            if mandate.spec_hash != mandate_spec_hash:
                # The buyer's claimed hash does not match this merchant's own
                # stored record -- a tampered or fabricated mandate reference,
                # never acted on regardless of what body the buyer sent.
                return _reject(trace_id, ReasonCode.MANDATE_TAMPERED)

            quote = await uow.quotes.get(quote_id)
            if quote is None or quote.mandate_id != mandate.mandate_id:
                return _reject(trace_id, ReasonCode.STALE_PRICE)
            if quote.quote_hash != quote_hash:
                return _reject(trace_id, ReasonCode.STALE_PRICE)

            item = await uow.catalog.get_item(quote.sku)
            if item is None:
                return _reject(trace_id, ReasonCode.STALE_PRICE)
            live_catalog_version = await uow.catalog.current_version()
            reserved_minor = await ledger_service.committed_total(uow, mandate.mandate_id)

            intent_draft = PurchaseIntent(
                currency=mandate.bounds.currency,
                category=item.category,
                merchant=item.merchant_id,
                unit_price_minor=quote.unit_price_minor,
                total_minor=quote.total_minor,
                nights=quote.nights,
                rooms=mandate.intent.rooms,
                refundable=quote.refundable,
                quoted_total_minor=quote.total_minor,
                current_total_minor=item.unit_price_minor * quote.nights,
                # The intent is hashed and bound against the terms this quote
                # actually pinned (§10.1 rule 11 / Gate G5's whole point), not
                # the live counter -- a legitimate buyer only ever knows their
                # own quote's own catalog_version, never a value the merchant
                # computes after the fact. Using `live_catalog_version` here
                # instead (as this line once did) made `domain.policy.rules.
                # catalog_freshness` compare `live_catalog_version` against
                # itself -- vacuously true, permanently defeating Gate G5's
                # own STALE_PRICE detection -- and independently caused every
                # genuinely stale propose to fail intent_hash verification
                # first (INTENT_MISMATCH), before the policy engine or the
                # gate ever ran at all. See docs/adr/0010 decision 15.
                catalog_version=quote.catalog_version,
                mandate_spec_hash=mandate.spec_hash or "",
                intent_hash="",
            )
            computed_intent_hash = compute_intent_hash(intent_draft)
            intent = intent_draft.model_copy(update={"intent_hash": computed_intent_hash})

            if computed_intent_hash != intent_hash:
                # The buyer's claimed intent_hash does not match what this
                # merchant independently derives from its own quote+mandate --
                # exactly the "altered mandate/intent body" attack §28 P7
                # instruction 5's negative test proves is inert.
                return _reject(trace_id, ReasonCode.INTENT_MISMATCH)

            decision_id = new_id("dec")
            ctx = PolicyContext(
                now=clock.now(),
                reserved_minor=reserved_minor,
                txn_count=0,
                catalog_version=live_catalog_version,
                decision_id=decision_id,
                decision_ttl_s=settings.decision_ttl_s,
            )
            decision = evaluate(mandate, intent, ctx)
            reason = decision.reason_codes[0] if decision.reason_codes else ReasonCode.OK
            metrics.decisions_total.labels(verdict=decision.verdict, reason=str(reason)).inc()
            await uow.decisions.add(decision)
            await append_entry(
                uow,
                trace_id=trace_id,
                actor_type="agent",
                actor_id=actor_id,
                action=AuditAction.ORDER_PROPOSED,
                subject={"mandate_id": mandate.mandate_id, "quote_id": quote_id},
                payload={
                    "mandate_id": mandate.mandate_id,
                    "quote_id": quote_id,
                    "verdict": decision.verdict,
                    "reason_codes": [str(c) for c in decision.reason_codes],
                },
            )
            await uow.commit()

        if decision.verdict != "ALLOW":
            return _reject(trace_id, decision.reason_codes[0])

        req = MoneyActionRequest(
            trace_id=trace_id,
            mandate_id=mandate.mandate_id,
            decision_id=decision.decision_id,
            quote_id=quote_id,
            intent_hash=computed_intent_hash,
            amount_minor=quote.total_minor,
            currency=quote.currency,
            attempt_no=1,
            actor_id=actor_id,
        )
        snapshot = await begin_purchase(req, session_factory, provider, clock, breaker)
        if snapshot.status == "AWAITING_AUTHORIZATION":
            return HandlerResult(
                type="order.propose",
                body={
                    "decision": "accept",
                    "order_id": snapshot.order_id,
                    "saga_id": snapshot.saga_id,
                },
            )
        return _reject(trace_id, snapshot.reason_code or ReasonCode.INTERNAL_ERROR)


def _reject(trace_id: str, reason_code: ReasonCode) -> HandlerResult:
    return HandlerResult(
        type="order.propose",
        body={"decision": "reject", "reason_code": str(reason_code), "trace_id": trace_id},
    )


async def handle_order_status(uow: UnitOfWork, *, order_id: str) -> HandlerOutcome:
    with tracing.span("use_case.order_status", order_id=order_id):
        order = await uow.orders.get(order_id)
        if order is None:
            return HandlerError(ReasonCode.ORDER_NOT_FOUND, f"no order {order_id}")
        seq_range = await uow.audit_log.get_seq_range_for_order(order_id)
        return HandlerResult(
            type="order.status",
            body={
                "order_id": order.id,
                "status": order.status,
                "amount_minor": order.amount_minor,
                "currency": order.currency,
                "provider_payment_id": order.provider_payment_id,
                "audit_seq_from": seq_range[0] if seq_range else None,
                "audit_seq_to": seq_range[1] if seq_range else None,
            },
        )


async def handle_receipt_issue(uow: UnitOfWork, *, order_id: str) -> HandlerOutcome:
    with tracing.span("use_case.receipt_issue", order_id=order_id):
        order = await uow.orders.get(order_id)
        if order is None:
            return HandlerError(ReasonCode.ORDER_NOT_FOUND, f"no order {order_id}")
        if order.status != "CAPTURED":
            return HandlerError(
                ReasonCode.ORDER_NOT_SETTLED,
                f"order {order_id} is not yet settled",
                retryable=True,
            )
        seq_range = await uow.audit_log.get_seq_range_for_order(order_id)
        return HandlerResult(
            type="receipt.issue",
            body={
                "order_id": order.id,
                "payment_id": order.provider_payment_id,
                "amount_minor": order.amount_minor,
                "currency": order.currency,
                "audit_seq_from": seq_range[0] if seq_range else None,
                "audit_seq_to": seq_range[1] if seq_range else None,
            },
        )


def build_response_envelope(
    request: AgentEnvelope, outcome: HandlerOutcome, clock: Clock
) -> AgentEnvelope:
    """Every agent-to-agent message is signed (§14.1) -- responses are no
    exception. Signed with this process's own merchant identity
    (`settings.merchant_*`), addressed back to whoever the *verified*
    request came from (`request.from_` -- only ever reached after
    `envelope_service.verify_envelope` has already confirmed that claim)."""
    if isinstance(outcome, HandlerError):
        response_type: MessageType = "error"
        body: dict[str, object] = {
            "reason_code": str(outcome.reason_code),
            "message": outcome.message,
            "retryable": outcome.retryable,
        }
    else:
        response_type = outcome.type
        body = outcome.body

    draft = AgentEnvelope.model_validate(
        {
            "protocol": PROTOCOL_ID,
            "msg_id": new_id("msg"),
            "ts": clock.now(),
            "from": settings.merchant_agent_id,
            "to": request.from_,
            "corr_id": request.corr_id,
            "type": response_type,
            "body": body,
        }
    )
    private_key = Ed25519PrivateKey.from_private_bytes(
        bytes.fromhex(settings.merchant_private_key_hex)
    )
    return sign_envelope_ed25519(draft, private_key, settings.merchant_key_id)
