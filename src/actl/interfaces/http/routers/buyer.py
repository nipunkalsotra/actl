"""§28 P12 buyer frontend: thin, typed REST routes for the one thing the
existing surface doesn't already offer a browser -- signing/locking a
mandate (mandate issuance is normally the buyer-agent's own system, out of
scope per README §Limitations) and reaching `order.propose`/checkout
without an Ed25519-signed agent envelope (`/agent/v1/messages` is for
agent-to-agent traffic; a browser tab is this merchant's own first-party
buyer client, not a third-party agent).

Every route here is a thin composition of already-tested application
services -- `create_quote`, `list_catalog`, `rank_candidates`,
`extract_mandate_draft`, `handle_order_propose`, `saga.complete_purchase`,
`explain_order` -- in the exact call shape `application.demo` already
uses as its own reference implementation. No gate, saga, policy, or audit
rule is reimplemented here; `handle_order_propose` still independently
recomputes and compares every hash against this merchant's own database
before admitting anything, regardless of what this router passes in.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application.agents import merchant
from actl.application.agents.merchant import HandlerError, handle_order_propose
from actl.application.catalog_service import CatalogQuery, create_quote, list_catalog
from actl.application.conversation.extraction import extract_mandate_draft
from actl.application.conversation.ranking import rank_candidates
from actl.application.explain_service import OrderNotFoundForExplain, explain_order
from actl.application.orchestrator import saga
from actl.application.ports import LLMClient, PaymentProvider
from actl.application.upsell_service import list_eligible_offers, price_offer_for_purchase
from actl.config import settings
from actl.domain.mandate.draft import ClarificationNeeded, MandateDraftSlots
from actl.domain.mandate.hashing import compute_spec_hash
from actl.domain.mandate.models import (
    Delegate,
    Mandate,
    MandateBounds,
    MandateControls,
    MandateIntent,
    MandateSignature,
    MandateTemporal,
    Principal,
)
from actl.domain.mandate.signing import sign_spec_hash
from actl.domain.mandate.state_machine import MandateStatus
from actl.domain.policy.rules import PurchaseIntent, compute_intent_hash
from actl.infrastructure.db.uow import UnitOfWork
from actl.infrastructure.llm.health import LLMHealth
from actl.interfaces.http.deps import (
    get_breaker,
    get_clock,
    get_llm_client,
    get_llm_health,
    get_payment_provider,
    get_session_factory,
    get_uow,
)
from actl.interfaces.http.routers.audit import render_explain_result
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import Clock
from actl.platform.ids import new_id

router = APIRouter(tags=["buyer"])

_NO_NUL_BYTES = r"^[^\x00]*$"
_GOA_LOCATION = "Goa, IN"
_CATEGORY = "travel.hotel"


@router.get("/buyer/v1/config")
async def get_config(health: LLMHealth = Depends(get_llm_health)) -> dict[str, Any]:
    """Non-secret, public config only -- `razorpay_key_id` is the
    published, embed-in-client-side-checkout.js identifier Razorpay's own
    model treats as public; the matching key *secret* never leaves
    `config.py`/the payment adapter.

    `llm_status` never claims Groq is available merely because
    LLM_ENABLED=true and a key is configured -- "groq_healthy" requires
    `health.succeeded_once`, set only after a real request the adapter
    itself already made actually completed (never a health check added
    here; this route makes no LLM call of its own)."""
    is_razorpay = settings.payment_provider == "razorpay"
    if not settings.llm_enabled:
        llm_status = "deterministic"
    elif health.succeeded_once:
        llm_status = "groq_healthy"
    else:
        llm_status = "groq_configured"
    return {
        "currency": "INR",
        "location": _GOA_LOCATION,
        "payment_provider": settings.payment_provider,
        "razorpay_key_id": settings.razorpay_key_id if is_razorpay else None,
        "quote_ttl_s": settings.quote_ttl_s,
        "llm_status": llm_status,
    }


# ---------------------------------------------------------------------------
# Catalog (optionally mandate-ranked)
# ---------------------------------------------------------------------------


@router.get("/buyer/v1/catalog")
async def get_buyer_catalog(
    mandate_id: Annotated[str | None, Query(pattern=_NO_NUL_BYTES)] = None,
    uow: UnitOfWork = Depends(get_uow),
    clock: Clock = Depends(get_clock),
    llm: LLMClient = Depends(get_llm_client),
) -> dict[str, Any]:
    feed = await list_catalog(
        uow,
        clock,
        CatalogQuery(
            category=_CATEGORY,
            location_city="Goa",
            location_country="IN",
            is_buyer_listable=True,
            limit=50,
        ),
        actor_id="web_buyer",
    )
    items = list(feed.items)
    ranked = False
    degraded: bool | None = None

    if mandate_id:
        loaded = await uow.mandates.get(mandate_id)
        if loaded is not None:
            mandate, _status = loaded
            result = await rank_candidates(llm, items, mandate)
            items = result.items
            ranked = True
            degraded = result.degraded

    return {
        "catalog_version": feed.catalog_version,
        "generated_at": feed.generated_at.isoformat(),
        "currency": feed.currency,
        "ranked": ranked,
        "degraded": degraded,
        "items": [item.model_dump(mode="json", by_alias=True) for item in items],
    }


# ---------------------------------------------------------------------------
# Mandate: U1 extraction, then explicit lock/sign
# ---------------------------------------------------------------------------


class ExtractRequest(BaseModel):
    conversation_text: str = Field(min_length=1, max_length=4000)


def _serialize_slots(slots: MandateDraftSlots) -> dict[str, Any]:
    """Shared by both extract_mandate branches so a partial draft
    (clarification_needed) and a complete one (draft_ready) expose the
    same shape -- the browser needs "what's already understood" either
    way to render a progressive acknowledgement instead of a static
    all-fields prompt."""
    return {
        "category": slots.category,
        "location": slots.location,
        "check_in": slots.check_in,
        "nights": slots.nights,
        "rooms": slots.rooms,
        "currency": slots.currency,
        "guests": slots.guests,
        "refundable": slots.refundable,
    }


@router.post("/buyer/v1/mandate/extract")
async def extract_mandate(
    body: ExtractRequest, llm: LLMClient = Depends(get_llm_client)
) -> dict[str, Any]:
    """§17.1 U1, real extraction (Groq if configured, else the same
    deterministic `LLMUnavailable` fallback the CLI/demo/worker share) --
    never invents a bound: a missing/unverifiable money value always comes
    back as a clarification question, never a guessed number."""
    result = await extract_mandate_draft(llm, body.conversation_text)
    if isinstance(result, ClarificationNeeded):
        return {
            "status": "clarification_needed",
            "missing_slots": list(result.missing_slots),
            "questions": list(result.questions),
            "slots": _serialize_slots(result.slots),
            "max_total_minor": result.max_total_minor,
        }
    return {
        "status": "draft_ready",
        "max_total_minor": result.max_total_minor,
        "max_unit_minor": result.max_unit_minor,
        "slots": _serialize_slots(result.slots),
    }


class MandateCreateRequest(BaseModel):
    nights: int = Field(gt=0, le=30)
    rooms: int = Field(gt=0, le=10)
    max_total_minor: int = Field(gt=0)
    require_refundable: bool = True
    check_in: str = Field(min_length=1, max_length=32, pattern=_NO_NUL_BYTES)


@router.post("/buyer/v1/mandate", status_code=201)
async def create_mandate(
    body: MandateCreateRequest,
    uow: UnitOfWork = Depends(get_uow),
    clock: Clock = Depends(get_clock),
) -> dict[str, Any]:
    """Builds, signs (HMAC-SHA256 over spec_hash, §14.1's documented
    development-fallback algorithm -- this is a 100% test-mode build) and
    locks a real Mandate row -- the exact `demo.py::_build_mandate` shape,
    parameterised from the buyer's own confirmed bounds instead of fixed
    demo constants. LOCKED immediately: this route *is* the human
    confirmation step (the browser only calls it from the mandate-review
    card's own explicit "Lock & sign mandate" action)."""
    now = clock.now()
    max_unit_minor = max(body.max_total_minor // body.nights, 1)
    draft = Mandate(
        mandate_id=new_id("mdt"),
        version=1,
        principal=Principal(type="human", id="usr_web_buyer"),
        delegate=Delegate(type="agent", id="agt_web_buyer", key_id="ed25519:web-buyer"),
        intent=MandateIntent(
            category=_CATEGORY,
            location=_GOA_LOCATION,
            check_in=body.check_in,
            nights=body.nights,
            rooms=body.rooms,
        ),
        bounds=MandateBounds(
            currency="INR",
            max_total_minor=body.max_total_minor,
            max_unit_minor=max_unit_minor,
            max_transactions=1,
            allowed_categories=[_CATEGORY],
            blocked_merchants=[],
            require_refundable=body.require_refundable,
            max_price_delta_bps=1000,
        ),
        temporal=MandateTemporal(
            not_before=now,
            expires_at=now + timedelta(seconds=settings.mandate_default_ttl_s),
            quote_ttl_s=settings.quote_ttl_s,
        ),
        controls=MandateControls(human_confirm_required=True, revocable=True),
    )
    spec_hash = compute_spec_hash(draft)
    signature = MandateSignature(
        alg="HMAC-SHA256",
        key_id="mk_web_buyer",
        value=sign_spec_hash(spec_hash, settings.mandate_signing_key.encode("utf-8")),
    )
    mandate = draft.model_copy(update={"spec_hash": spec_hash, "signature": signature})

    await uow.mandates.add(mandate, MandateStatus.LOCKED)
    await uow.commit()

    return {
        "mandate_id": mandate.mandate_id,
        "spec_hash": mandate.spec_hash,
        "status": MandateStatus.LOCKED.value,
        "intent": mandate.intent.model_dump(mode="json"),
        "bounds": mandate.bounds.model_dump(mode="json"),
        "expires_at": mandate.temporal.expires_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# Order propose + checkout (the real P6/P7 gate/saga, no envelope needed --
# this is a first-party browser client of this merchant's own service, not
# an outside agent).
# ---------------------------------------------------------------------------


class OrderProposeRequest(BaseModel):
    quote_id: str = Field(min_length=1, pattern=_NO_NUL_BYTES)
    mandate_id: str = Field(min_length=1, pattern=_NO_NUL_BYTES)


@router.post("/buyer/v1/order/propose")
async def propose_order(
    body: OrderProposeRequest,
    clock: Clock = Depends(get_clock),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    provider: PaymentProvider = Depends(get_payment_provider),
    breaker: CircuitBreaker = Depends(get_breaker),
) -> dict[str, Any]:
    trace_id = new_id("trc")
    async with UnitOfWork(session_factory) as uow:
        loaded = await uow.mandates.get(body.mandate_id)
        if loaded is None:
            raise HTTPException(status_code=404, detail="mandate not found")
        mandate, _status = loaded
        quote = await uow.quotes.get(body.quote_id)
        if quote is None:
            raise HTTPException(status_code=404, detail="quote not found")
        item = await uow.catalog.get_item(quote.sku)
        if item is None:
            raise HTTPException(status_code=404, detail="catalog item not found")

    # Same PurchaseIntent construction `application.demo._propose_and_settle`
    # uses -- every field sourced from this merchant's own quote/mandate/
    # catalog records, never from anything the browser claims.
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
        catalog_version=quote.catalog_version,
        mandate_spec_hash=mandate.spec_hash or "",
        intent_hash="",
    )
    intent_hash = compute_intent_hash(intent_draft)

    outcome = await handle_order_propose(
        session_factory,
        provider,
        clock,
        breaker,
        quote_id=quote.id,
        quote_hash=quote.quote_hash,
        mandate_id=mandate.mandate_id,
        mandate_spec_hash=mandate.spec_hash or "",
        intent_hash=intent_hash,
        trace_id=trace_id,
        actor_id="web_buyer",
    )
    return dict(outcome.body)


class CheckoutRequest(BaseModel):
    order_id: str = Field(min_length=1, pattern=_NO_NUL_BYTES)
    saga_id: str = Field(min_length=1, pattern=_NO_NUL_BYTES)
    # Only meaningful for a real Razorpay Checkout.js callback -- the
    # browser posts back exactly what checkout.js handed it (order id,
    # payment id, and Razorpay's own signature), never a secret. Left
    # unset for PAYMENT_PROVIDER=simulator, where this route synthesizes
    # the equivalent real, valid signature itself via the adapter's own
    # `build_checkout_payload` test helper -- there is no separate
    # checkout UI to embed for a network-free deterministic provider.
    provider_payment_id: str | None = None
    provider_signature: str | None = None


@router.post("/buyer/v1/checkout")
async def checkout(
    body: CheckoutRequest,
    clock: Clock = Depends(get_clock),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    provider: PaymentProvider = Depends(get_payment_provider),
    breaker: CircuitBreaker = Depends(get_breaker),
) -> dict[str, Any]:
    async with UnitOfWork(session_factory) as uow:
        order = await uow.orders.get(body.order_id)
    if order is None or order.provider_order_id is None:
        raise HTTPException(status_code=404, detail="order not found")

    payment_id = body.provider_payment_id
    signature = body.provider_signature
    if payment_id is None or signature is None:
        build_checkout_payload = getattr(provider, "build_checkout_payload", None)
        if build_checkout_payload is None:
            raise HTTPException(
                status_code=400,
                detail="provider_payment_id/provider_signature required for this payment provider",
            )
        payments = await provider.fetch_payments(order.provider_order_id)
        if not payments:
            raise HTTPException(status_code=409, detail="no payment found for this order yet")
        payment = payments[0]
        payment_id = payment.id
        signature = build_checkout_payload(order.provider_order_id, payment_id)

    snapshot = await saga.complete_purchase(
        body.saga_id,
        session_factory,
        provider,
        clock,
        breaker,
        provider_order_id=order.provider_order_id,
        provider_payment_id=payment_id,
        provider_signature=signature,
        actor_id="web_buyer",
    )
    return {
        "saga_id": snapshot.saga_id,
        "mandate_id": snapshot.mandate_id,
        "status": snapshot.status,
        "step": snapshot.step,
        "order_id": snapshot.order_id,
        "reason_code": str(snapshot.reason_code) if snapshot.reason_code else None,
    }


@router.get("/buyer/v1/order/{order_id}")
async def get_order(order_id: str, uow: UnitOfWork = Depends(get_uow)) -> dict[str, Any]:
    outcome = await merchant.handle_order_status(uow, order_id=order_id)
    if isinstance(outcome, HandlerError):
        raise HTTPException(
            status_code=404,
            detail={"reason_code": str(outcome.reason_code), "message": outcome.message},
        )
    return dict(outcome.body)


# ---------------------------------------------------------------------------
# Audit proof, buyer-facing (their own order; no reviewer read-token needed)
# ---------------------------------------------------------------------------


@router.get("/buyer/v1/audit/explain/{order_id}")
async def buyer_explain(order_id: str, uow: UnitOfWork = Depends(get_uow)) -> dict[str, Any]:
    try:
        result = await explain_order(uow, order_id)
    except OrderNotFoundForExplain:
        raise HTTPException(
            status_code=404, detail={"reason_code": "ORDER_NOT_FOUND", "order_id": order_id}
        ) from None
    return render_explain_result(result)


# ---------------------------------------------------------------------------
# §28 P12 contextual upsell: real, buyer-driven post-booking add-on offers.
# Eligibility/pricing is 100% deterministic (application.upsell_service,
# no LLM call); a purchase always mints a brand-new, narrowly-scoped
# mandate and runs the exact same real quote -> gate -> saga -> ledger ->
# payment pipeline every other purchase in this app uses -- the settled
# base mandate is never reused (§9.1's SETTLED status is terminal; the
# gate's own G1 check would refuse it regardless).
# ---------------------------------------------------------------------------


def _serialize_offer(offer: Any) -> dict[str, Any]:
    return {
        "sku": offer.sku,
        "title": offer.title,
        "unit_price_minor": offer.unit_price_minor,
        "total_minor": offer.total_minor,
        "currency": offer.currency,
        "refundable": offer.refundable,
        "quantity_description": offer.quantity_description,
    }


@router.get("/buyer/v1/upsell/offers")
async def get_upsell_offers(
    order_id: Annotated[str, Query(min_length=1, pattern=_NO_NUL_BYTES)],
    uow: UnitOfWork = Depends(get_uow),
) -> dict[str, Any]:
    """Deterministic eligibility from real data only: the base order must
    be a settled travel.hotel purchase, each candidate add-on must have
    real available_units, and any add-on already purchased/declined for
    this exact base order is excluded (no duplicate offer, let alone
    duplicate purchase). Never returns a fabricated offer, and never
    calls an LLM -- an empty `offers` list is a legitimate, honest
    answer, not a degraded one."""
    result = await list_eligible_offers(uow, base_order_id=order_id)
    if result is None:
        return {"base_order_id": order_id, "currency": "INR", "offers": []}
    currency, offers = result
    return {
        "base_order_id": order_id,
        "currency": currency,
        "offers": [_serialize_offer(o) for o in offers],
    }


class UpsellDeclineRequest(BaseModel):
    base_order_id: str = Field(min_length=1, pattern=_NO_NUL_BYTES)


@router.post("/buyer/v1/upsell/decline")
async def decline_upsell(
    body: UpsellDeclineRequest, uow: UnitOfWork = Depends(get_uow)
) -> dict[str, Any]:
    """"No thanks" -- persists a non-sensitive analytics-only state
    change (offered -> declined) so the buyer is never asked again this
    session; never money-moving, never blocks anything."""
    await uow.addon_purchases.decline_all_offered(body.base_order_id)
    await uow.commit()
    return {"status": "declined"}


class UpsellPurchaseRequest(BaseModel):
    base_order_id: str = Field(min_length=1, pattern=_NO_NUL_BYTES)
    offer_sku: str = Field(min_length=1, pattern=_NO_NUL_BYTES)


@router.post("/buyer/v1/upsell/purchase")
async def purchase_upsell(
    body: UpsellPurchaseRequest,
    clock: Clock = Depends(get_clock),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    provider: PaymentProvider = Depends(get_payment_provider),
    breaker: CircuitBreaker = Depends(get_breaker),
) -> dict[str, Any]:
    """The buyer's deliberate "Approve" click on the separate review step
    -- never reachable merely by viewing offers. Price is always
    recomputed here from the live catalog, never taken from the request
    body. Builds and locks a brand-new mandate scoped to only this one
    add-on category (`allowed_categories=[offer.category]`), then runs
    the real create_quote -> handle_order_propose (gate) ->
    saga.complete_purchase pipeline unmodified. The addon_purchases row's
    UNIQUE(base_order_id, offer_sku) constraint makes a concurrent
    duplicate attempt (two clicks, two tabs) fail closed before any
    mandate is even built."""
    now = clock.now()
    trace_id = new_id("trc")
    addon_mandate_id = new_id("mdt")
    addon_purchase_id = new_id("adp")

    async with UnitOfWork(session_factory) as uow:
        offer = await price_offer_for_purchase(
            uow, base_order_id=body.base_order_id, offer_sku=body.offer_sku
        )
        if offer is None:
            raise HTTPException(status_code=409, detail={"reason_code": "OFFER_NOT_AVAILABLE"})

        got_lock = await uow.addon_purchases.try_mark_pending(
            id=addon_purchase_id,
            base_order_id=body.base_order_id,
            offer_sku=body.offer_sku,
            addon_mandate_id=addon_mandate_id,
            price_minor=offer.total_minor,
            currency=offer.currency,
        )
        if not got_lock:
            raise HTTPException(status_code=409, detail={"reason_code": "ALREADY_PURCHASED"})

        draft = Mandate(
            mandate_id=addon_mandate_id,
            version=1,
            principal=Principal(type="human", id="usr_web_buyer"),
            delegate=Delegate(type="agent", id="agt_web_buyer", key_id="ed25519:web-buyer"),
            intent=MandateIntent(
                category=offer.category,
                location=_GOA_LOCATION,
                check_in="addon",
                nights=offer.quantity,
                rooms=1,
            ),
            bounds=MandateBounds(
                currency=offer.currency,
                max_total_minor=offer.total_minor,
                max_unit_minor=max(offer.total_minor // offer.quantity, 1),
                max_transactions=1,
                allowed_categories=[offer.category],
                blocked_merchants=[],
                require_refundable=offer.refundable,
                max_price_delta_bps=1000,
            ),
            temporal=MandateTemporal(
                not_before=now,
                expires_at=now + timedelta(seconds=settings.mandate_default_ttl_s),
                quote_ttl_s=settings.quote_ttl_s,
            ),
            controls=MandateControls(human_confirm_required=True, revocable=True),
        )
        spec_hash = compute_spec_hash(draft)
        signature = MandateSignature(
            alg="HMAC-SHA256",
            key_id="mk_web_buyer",
            value=sign_spec_hash(spec_hash, settings.mandate_signing_key.encode("utf-8")),
        )
        mandate = draft.model_copy(update={"spec_hash": spec_hash, "signature": signature})
        await uow.mandates.add(mandate, MandateStatus.LOCKED)

        quote = await create_quote(
            uow,
            clock,
            mandate_id=mandate.mandate_id,
            sku=body.offer_sku,
            nights=offer.quantity,
            actor_id="web_buyer",
        )
        await uow.commit()

    # Same PurchaseIntent construction propose_order/demo.py use -- every
    # field sourced from this merchant's own quote/mandate/catalog
    # records, never from anything the browser claims.
    async with UnitOfWork(session_factory) as uow:
        item = await uow.catalog.get_item(quote.sku)
        assert item is not None  # just created the quote from this same sku
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
        catalog_version=quote.catalog_version,
        mandate_spec_hash=mandate.spec_hash or "",
        intent_hash="",
    )
    intent_hash = compute_intent_hash(intent_draft)

    outcome = await handle_order_propose(
        session_factory,
        provider,
        clock,
        breaker,
        quote_id=quote.quote_id,
        quote_hash=quote.quote_hash or "",
        mandate_id=mandate.mandate_id,
        mandate_spec_hash=mandate.spec_hash or "",
        intent_hash=intent_hash,
        trace_id=trace_id,
        actor_id="web_buyer",
    )
    propose_body = dict(outcome.body)
    if propose_body.get("decision") != "accept":
        async with UnitOfWork(session_factory) as uow:
            await uow.addon_purchases.mark_failed(
                base_order_id=body.base_order_id, offer_sku=body.offer_sku
            )
            await uow.commit()
        return {
            "decision": "reject",
            "reason_code": propose_body.get("reason_code"),
            "addon_order_id": None,
        }

    order_id = str(propose_body["order_id"])
    saga_id = str(propose_body["saga_id"])

    async with UnitOfWork(session_factory) as uow:
        order = await uow.orders.get(order_id)
    if order is None or order.provider_order_id is None:
        async with UnitOfWork(session_factory) as uow:
            await uow.addon_purchases.mark_failed(
                base_order_id=body.base_order_id, offer_sku=body.offer_sku
            )
            await uow.commit()
        raise HTTPException(status_code=409, detail={"reason_code": "ORDER_NOT_READY"})

    build_checkout_payload = getattr(provider, "build_checkout_payload", None)
    payments = await provider.fetch_payments(order.provider_order_id)
    if not payments or build_checkout_payload is None:
        async with UnitOfWork(session_factory) as uow:
            await uow.addon_purchases.mark_failed(
                base_order_id=body.base_order_id, offer_sku=body.offer_sku
            )
            await uow.commit()
        raise HTTPException(status_code=409, detail={"reason_code": "PAYMENT_NOT_READY"})
    payment = payments[0]
    signature_payload = build_checkout_payload(order.provider_order_id, payment.id)

    snapshot = await saga.complete_purchase(
        saga_id,
        session_factory,
        provider,
        clock,
        breaker,
        provider_order_id=order.provider_order_id,
        provider_payment_id=payment.id,
        provider_signature=signature_payload,
        actor_id="web_buyer",
    )

    async with UnitOfWork(session_factory) as uow:
        if snapshot.status == "COMPLETED":
            await uow.addon_purchases.mark_settled(
                base_order_id=body.base_order_id, offer_sku=body.offer_sku, addon_order_id=order_id
            )
        else:
            await uow.addon_purchases.mark_failed(
                base_order_id=body.base_order_id, offer_sku=body.offer_sku
            )
        await uow.commit()

    return {
        "decision": "accept" if snapshot.status == "COMPLETED" else "reject",
        "reason_code": str(snapshot.reason_code) if snapshot.reason_code else None,
        "addon_order_id": order_id if snapshot.status == "COMPLETED" else None,
    }
