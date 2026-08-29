"""§20 F1: "Price changes between quote and order -- Gate G5:
catalog_version mismatch -- Auto re-quote once, re-evaluate policy,
proceed or deny with real numbers."

Drives the exact same production path every real `order.propose` message
goes through: `application.agents.merchant.handle_order_propose` (§28
P7) -- the same function `interfaces/agent/routes.py` dispatches every
real HTTP `order.propose` envelope to (checked directly: `grep -n
"handle_order_propose" src/actl/interfaces/agent/routes.py`). This module
plays the role of the buyer's own client for both the stale attempt and
the retried attempt: it builds the same `intent_hash` a legitimate buyer
would (over the quote's own pinned data), submits it to `handle_order_
propose`, and -- only on a `STALE_PRICE` reject -- takes exactly one
fresh quote and retries once through the identical path. The only bypass
anywhere in this module (or in `tests/chaos/test_f1.py`) is the required
§20 F1 fault injection itself: the out-of-band `mutate_price` call that
creates a stale quote in the first place, which is an out-of-band admin
action no buyer or recovery orchestration could or should perform through
the normal catalog-admin endpoint (§28 P9 instruction 1).

`handle_order_propose` needed a one-line, root-cause fix for `STALE_PRICE`
to be reachable through it at all -- it was reconstructing the intent's
`catalog_version` from the *live* counter instead of the quote's own
pinned value, which independently (a) made the policy engine's `catalog.
freshness` rule compare the live counter against itself (vacuously true,
permanently defeating Gate G5) and (b) made intent_hash verification fail
first with `INTENT_MISMATCH` for any genuinely stale propose, before the
policy engine or the gate ever ran. See docs/adr/0010 decision 15 for the
full diagnosis; the fix landed directly in `handle_order_propose` itself,
fixing every caller, not just this one.

Used by `actl demo --scenario stale_price` and `tests/chaos/test_f1.py`.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application.agents.merchant import handle_order_propose
from actl.application.catalog_service import create_quote
from actl.application.gate import MoneyActionResult
from actl.domain.catalog.quote import Quote
from actl.domain.mandate.models import Mandate
from actl.domain.policy.reason_codes import ReasonCode
from actl.domain.policy.rules import PurchaseIntent, compute_intent_hash
from actl.infrastructure.db.uow import UnitOfWork
from actl.infrastructure.providers.simulator.adapter import SimulatorAdapter
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import Clock


@dataclass(frozen=True)
class RequoteOutcome:
    result: MoneyActionResult
    requoted: bool
    final_quote: Quote
    saga_id: str | None


async def _buyer_intent_hash(
    session_factory: async_sessionmaker[AsyncSession], mandate: Mandate, quote: Quote
) -> str:
    """Exactly what a legitimate buyer computes from its own quote --
    the same construction `handle_order_propose` itself independently
    re-derives and verifies against (matching `application.growth.
    simulation._attempt_purchase` and `tests/chaos/test_f6.py`'s own
    established buyer-side precedent)."""
    async with UnitOfWork(session_factory) as uow:
        item = await uow.catalog.get_item(quote.sku)
    assert item is not None
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
    return compute_intent_hash(intent_draft)


async def _propose(
    session_factory: async_sessionmaker[AsyncSession],
    provider: SimulatorAdapter,
    clock: Clock,
    breaker: CircuitBreaker,
    *,
    mandate: Mandate,
    quote: Quote,
    actor_id: str,
    trace_id: str,
) -> tuple[MoneyActionResult, str | None]:
    """One real `order.propose` through `handle_order_propose` -- the
    live Money Action Gate and saga run on ALLOW, exactly as they would
    for any other caller. Returns `(result, saga_id)`."""
    assert quote.quote_hash is not None
    intent_hash = await _buyer_intent_hash(session_factory, mandate, quote)
    outcome = await handle_order_propose(
        session_factory, provider, clock, breaker,
        quote_id=quote.quote_id, quote_hash=quote.quote_hash,
        mandate_id=mandate.mandate_id, mandate_spec_hash=mandate.spec_hash or "",
        intent_hash=intent_hash, trace_id=trace_id, actor_id=actor_id,
    )
    if outcome.body.get("decision") == "accept":
        order_id = str(outcome.body["order_id"])
        saga_id = str(outcome.body["saga_id"])
        return (
            MoneyActionResult(
                verdict="ALLOW", reason_code=ReasonCode.OK, trace_id=trace_id, order_id=order_id
            ),
            saga_id,
        )
    reason_code = ReasonCode(str(outcome.body.get("reason_code")))
    return MoneyActionResult(verdict="DENY", reason_code=reason_code, trace_id=trace_id), None


async def propose_with_one_requote_on_stale_price(
    session_factory: async_sessionmaker[AsyncSession],
    provider: SimulatorAdapter,
    clock: Clock,
    breaker: CircuitBreaker,
    *,
    mandate: Mandate,
    quote: Quote,
    actor_id: str,
    trace_id: str,
) -> RequoteOutcome:
    """§20 F1. `quote` is whatever was already pinned (obtained *before*
    an out-of-band price change, so this first attempt can genuinely hit
    `STALE_PRICE`). If (and only if) the result is a `STALE_PRICE` deny,
    takes exactly one fresh quote at the catalog's *current* price/version
    and proposes once more, through the identical `handle_order_propose`
    path -- "proceed or deny with real numbers" either way. Never loops: a
    second `STALE_PRICE` (or any other outcome) after the retry is
    returned as-is, not retried again."""
    first_result, first_saga_id = await _propose(
        session_factory, provider, clock, breaker,
        mandate=mandate, quote=quote, actor_id=actor_id, trace_id=trace_id,
    )
    if not (first_result.verdict == "DENY" and first_result.reason_code == ReasonCode.STALE_PRICE):
        return RequoteOutcome(
            result=first_result, requoted=False, final_quote=quote, saga_id=first_saga_id
        )

    async with UnitOfWork(session_factory) as uow:
        fresh_quote = await create_quote(
            uow,
            clock,
            mandate_id=mandate.mandate_id,
            sku=quote.sku,
            nights=quote.nights,
            actor_id=actor_id,
        )
        await uow.commit()

    second_result, second_saga_id = await _propose(
        session_factory, provider, clock, breaker,
        mandate=mandate, quote=fresh_quote, actor_id=actor_id, trace_id=trace_id,
    )
    return RequoteOutcome(
        result=second_result, requoted=True, final_quote=fresh_quote, saga_id=second_saga_id
    )
