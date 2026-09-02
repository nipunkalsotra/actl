"""§28 P8 instruction 9 / §22.2: `actl growth --seed demo --sessions N`.

Runs N seeded "baseline" (upsell-off) and N seeded "upsell" (upsell-on)
sessions through the *real* deterministic P4-P7 flow -- `create_quote`,
`handle_order_propose` (which runs the real Money Action Gate and saga),
`saga.complete_purchase` -- against the `SimulatorAdapter` only. Never
Razorpay, never Groq: the upsell offer/accept decision and whether a
session converts at all are both driven by `random.Random(seed)`, never
an LLM call (§22.2's own "seeded, two-arm sessions" scripted-conversation
design predates and does not require U1/U2/U3 at all).

§22.2: "Every accepted upsell is still just an order. There is no
separate code path or relaxed check for upsell revenue; it reaches the
gate and the ledger exactly like the base purchase." An upsell attempt
that would exceed the mandate's remaining budget is genuinely denied by
G4 (`BUDGET_EXCEEDED`) here, the same gate every other phase's money
action goes through -- never special-cased to always succeed.

Every stochastic decision derives from `random.Random(f"{seed}:...")`;
Python's str-seeded `random.Random` is deterministic across processes and
machines (unaffected by `PYTHONHASHSEED`), so a given `--seed`/`--sessions`
always produces byte-identical arm statistics. Row identifiers
(mandate_id, order_id, ...) still use the platform's normal `new_id()`
ULIDs -- *not* derived from the seed -- so the same seed can be re-run
against a live database without a primary-key collision; reproducibility
is a property of the computed statistics, not of the underlying row ids.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application.agents.merchant import handle_order_propose
from actl.application.catalog_service import create_quote
from actl.application.growth.events import (
    ARM_BASELINE,
    ARM_UPSELL,
    emit_order_completed,
    emit_session_started,
    emit_upsell_accepted,
    emit_upsell_offered,
)
from actl.application.orchestrator import saga
from actl.config import settings
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
from actl.infrastructure.db.repositories.catalog import CatalogItemRecord
from actl.infrastructure.db.uow import UnitOfWork
from actl.infrastructure.providers.simulator.adapter import SimulatorAdapter
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import Clock
from actl.platform.ids import new_id

BASE_SKU = "HTL-GROWTH-BASE"
UPSELL_SKU = "HTL-GROWTH-UPSELL"
BASE_NIGHTS = 3
UPSELL_NIGHTS = 3
CONVERSION_PROBABILITY = 0.75
UPSELL_ACCEPT_PROBABILITY = 0.55
BASE_MANDATE_BUDGET_MINOR = 900_000  # comfortably covers the 750000 base purchase
# The upsell item (270000/night x 3 nights = 810000) costs more than the
# base purchase (750000) -- a real upsell, pulling AOV up whenever it
# succeeds, not down. Its mandate's budget is drawn per-session from this
# range, representing the human's own varying approved add-on ceiling:
# roughly the lower half genuinely denies it at G4, the upper half
# genuinely admits it -- real, seed-deterministic bound enforcement, not
# a foregone conclusion either way.
UPSELL_MANDATE_BUDGET_RANGE_MINOR = (650_000, 1_000_000)


@dataclass(frozen=True)
class SessionOutcome:
    session_id: str
    arm: str
    converted: bool
    base_order_id: str | None
    upsell_offered: bool
    upsell_accepted: bool
    upsell_order_id: str | None


async def _seed_catalog(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Idempotent: `upsert_item` updates in place, matching scripts/seed.py's
    own precedent -- safe to call before every session.

    `upsert_item` does not touch `catalog_meta`'s global version counter
    (it is scripts/seed.py-only idempotent seeding, by design -- see its
    own docstring); only `mutate_price()` bumps that counter, stamping
    the mutated item's row with the new value in the same transaction.
    `create_quote` stamps a Quote's `catalog_version` from the *item's
    own* `version` column, while `handle_order_propose` later compares
    that against `current_version()` (the global counter) to detect catalog
    drift (§10.1 rule 11, STALE_PRICE/INTENT_MISMATCH). If some earlier,
    unrelated price mutation elsewhere in this database has already
    advanced the global counter past 1, seeding these items at a
    hardcoded `version=1` would make *every* growth-simulation purchase
    fail that check -- so the seeded items are stamped with whatever the
    global counter actually is right now, not an assumed starting value."""
    async with UnitOfWork(session_factory) as uow:
        current_version = await uow.catalog.current_version()
        await uow.catalog.upsert_item(
            CatalogItemRecord(
                sku=BASE_SKU,
                category="travel.hotel",
                merchant_id="mrc_growth_demo",
                unit="night",
                unit_price_minor=250000,
                available_units=999,
                location_city="Goa",
                location_country="IN",
                rating=4.3,
                sea_facing=True,
                breakfast_included=True,
                refundable=True,
                cancellation_window_h=48,
                instant_confirm=True,
                taxes_included=True,
                quote_required=True,
                version=current_version,
            )
        )
        await uow.catalog.upsert_item(
            CatalogItemRecord(
                sku=UPSELL_SKU,
                category="travel.hotel",
                merchant_id="mrc_growth_demo",
                unit="night",
                unit_price_minor=270000,
                available_units=999,
                location_city="Goa",
                location_country="IN",
                rating=4.6,
                sea_facing=True,
                breakfast_included=True,
                refundable=True,
                cancellation_window_h=48,
                instant_confirm=True,
                taxes_included=True,
                quote_required=True,
                version=current_version,
            )
        )
        await uow.commit()


def _build_mandate(*, max_total_minor: int, nights: int) -> Mandate:
    """A fresh, uniquely-ided, locked Mandate -- same shape/signing path
    production code uses (`settings.mandate_signing_key`), matching
    `tests/integration/db/conftest.py::make_locked_mandate`'s own
    precedent, reimplemented here since application code cannot import
    from `tests/`. `nights` must match whichever purchase this mandate is
    meant to authorize (§10.1 rule 8, `quantity.match`: the intent's
    nights/rooms must equal the *mandate's own* declared intent, not just
    fit under a price cap) -- the base and upsell mandates need different
    values since `BASE_NIGHTS != UPSELL_NIGHTS`.

    §9.1: EXECUTING -> SETTLED is terminal -- `saga.complete_purchase`'s
    S5 step transitions the mandate to SETTLED the moment *any* order
    against it settles, and G1 then refuses every later order.propose
    (MANDATE_INVALID) regardless of `max_transactions`. One mandate can
    therefore ever fund exactly one settled purchase; `max_transactions`
    bounds retries/attempts *before* that terminal transition, not a
    count of separate completed purchases. `run_session` mints a second,
    independent mandate for the upsell attempt for exactly this reason --
    matching the mandate model's own philosophy that each bounded
    purchase decision the human confirms is its own authorization."""
    draft = Mandate(
        mandate_id=new_id("mdt"),
        version=1,
        principal=Principal(type="human", id="usr_growth_demo"),
        delegate=Delegate(type="agent", id="agt_growth_demo", key_id="ed25519:growth-demo"),
        intent=MandateIntent(
            category="travel.hotel",
            location="Goa, IN",
            check_in="2026-09-12",
            nights=nights,
            rooms=1,
        ),
        bounds=MandateBounds(
            currency="INR",
            max_total_minor=max_total_minor,
            max_unit_minor=300000,
            max_transactions=1,
            allowed_categories=["travel.hotel"],
            blocked_merchants=[],
            require_refundable=True,
            max_price_delta_bps=0,
        ),
        temporal=MandateTemporal(
            not_before=datetime(2026, 1, 1, tzinfo=UTC),
            expires_at=datetime(2027, 1, 1, tzinfo=UTC),
            quote_ttl_s=120,
        ),
        controls=MandateControls(human_confirm_required=True, revocable=True),
    )
    spec_hash = compute_spec_hash(draft)
    signature = MandateSignature(
        alg="HMAC-SHA256",
        key_id="mk_growth_demo",
        value=sign_spec_hash(spec_hash, settings.mandate_signing_key.encode("utf-8")),
    )
    return draft.model_copy(update={"spec_hash": spec_hash, "signature": signature})


async def _attempt_purchase(
    session_factory: async_sessionmaker[AsyncSession],
    provider: SimulatorAdapter,
    clock: Clock,
    breaker: CircuitBreaker,
    *,
    mandate: Mandate,
    sku: str,
    nights: int,
    actor_id: str,
    trace_id: str,
) -> tuple[str, int] | None:
    """One purchase attempt through the real gate + saga. Returns
    `(order_id, total_minor)` on a settled purchase, or None if the gate
    denied it (e.g. BUDGET_EXCEEDED) or the saga did not complete -- both
    are legitimate outcomes here, never an exception."""
    async with UnitOfWork(session_factory) as uow:
        quote = await create_quote(
            uow, clock, mandate_id=mandate.mandate_id, sku=sku, nights=nights, actor_id=actor_id
        )
        await uow.commit()
        item = await uow.catalog.get_item(sku)
    assert item is not None
    assert quote.quote_hash is not None

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
        quote_hash=quote.quote_hash,
        mandate_id=mandate.mandate_id,
        mandate_spec_hash=mandate.spec_hash or "",
        intent_hash=intent_hash,
        trace_id=trace_id,
        actor_id=actor_id,
    )
    if outcome.body.get("decision") != "accept":
        return None
    order_id = str(outcome.body["order_id"])
    saga_id = str(outcome.body["saga_id"])

    async with UnitOfWork(session_factory) as uow:
        order = await uow.orders.get(order_id)
        # §28 P12: every order this seeded A/B session creates is
        # synthetic growth-simulation data, never a real customer
        # purchase -- tagged so merchant KPIs' organic gross sales and
        # Live Orders' display never present it as one (migration 0010).
        await uow.orders.set_source(order_id, "growth_simulation")
        await uow.commit()
    assert order is not None and order.provider_order_id is not None
    payments = await provider.fetch_payments(order.provider_order_id)
    payment = payments[0]
    signature = provider.build_checkout_payload(order.provider_order_id, payment.id)
    result = await saga.complete_purchase(
        saga_id,
        session_factory,
        provider,
        clock,
        breaker,
        provider_order_id=order.provider_order_id,
        provider_payment_id=payment.id,
        provider_signature=signature,
        actor_id=actor_id,
    )
    if result.status != "COMPLETED":
        return None
    return order_id, quote.total_minor


async def run_session(
    session_factory: async_sessionmaker[AsyncSession],
    provider: SimulatorAdapter,
    clock: Clock,
    breaker: CircuitBreaker,
    *,
    seed: str,
    index: int,
    arm: str,
) -> SessionOutcome:
    """One seeded session for one arm. `convert_rng` is derived from
    `seed`+`index` *only* (never `arm`) so the base-purchase conversion
    decision is identical between the baseline and upsell arms at the
    same index -- §22.2: "nothing about the market changed between runs,
    only the agent's behaviour did." The upsell accept decision has its
    own, arm-scoped rng."""
    session_id = new_id("sess")
    actor_id = f"agt_growth_{arm}_{index}"

    async with UnitOfWork(session_factory) as uow:
        await emit_session_started(uow, session_id=session_id, arm=arm)
        await uow.commit()

    convert_rng = random.Random(f"{seed}:convert:{index}")
    converted = convert_rng.random() < CONVERSION_PROBABILITY
    if not converted:
        return SessionOutcome(session_id, arm, False, None, False, False, None)

    mandate = _build_mandate(max_total_minor=BASE_MANDATE_BUDGET_MINOR, nights=BASE_NIGHTS)
    async with UnitOfWork(session_factory) as uow:
        await uow.mandates.add(mandate, MandateStatus.LOCKED)
        await uow.commit()

    base = await _attempt_purchase(
        session_factory,
        provider,
        clock,
        breaker,
        mandate=mandate,
        sku=BASE_SKU,
        nights=BASE_NIGHTS,
        actor_id=actor_id,
        trace_id=new_id("trc"),
    )
    if base is None:
        return SessionOutcome(session_id, arm, True, None, False, False, None)
    base_order_id, base_total_minor = base

    async with UnitOfWork(session_factory) as uow:
        await emit_order_completed(
            uow,
            session_id=session_id,
            arm=arm,
            order_id=base_order_id,
            total_minor=base_total_minor,
            currency="INR",
        )
        await uow.commit()

    if arm != ARM_UPSELL:
        return SessionOutcome(session_id, arm, True, base_order_id, False, False, None)

    async with UnitOfWork(session_factory) as uow:
        await emit_upsell_offered(uow, session_id=session_id, arm=arm, sku=UPSELL_SKU)
        await uow.commit()

    accept_rng = random.Random(f"{seed}:upsell:{index}")
    accepted = accept_rng.random() < UPSELL_ACCEPT_PROBABILITY
    if not accepted:
        return SessionOutcome(session_id, arm, True, base_order_id, True, False, None)

    async with UnitOfWork(session_factory) as uow:
        await emit_upsell_accepted(uow, session_id=session_id, arm=arm, sku=UPSELL_SKU)
        await uow.commit()

    # A second, independent mandate -- §9.1's EXECUTING -> SETTLED
    # transition is terminal (see `_build_mandate`'s own docstring), so
    # the base mandate cannot fund a second purchase once it has settled.
    # Its budget is seed-derived, genuinely varying whether it covers the
    # fixed-price upsell item -- §22.2: reaches the *same* gate and ledger
    # as the base purchase, so a BUDGET_EXCEEDED denial here is real,
    # not simulated, bound enforcement.
    budget_rng = random.Random(f"{seed}:upsell_budget:{index}")
    upsell_budget = budget_rng.randint(*UPSELL_MANDATE_BUDGET_RANGE_MINOR)
    upsell_mandate = _build_mandate(max_total_minor=upsell_budget, nights=UPSELL_NIGHTS)
    async with UnitOfWork(session_factory) as uow:
        await uow.mandates.add(upsell_mandate, MandateStatus.LOCKED)
        await uow.commit()

    upsell = await _attempt_purchase(
        session_factory,
        provider,
        clock,
        breaker,
        mandate=upsell_mandate,
        sku=UPSELL_SKU,
        nights=UPSELL_NIGHTS,
        actor_id=actor_id,
        trace_id=new_id("trc"),
    )
    if upsell is None:
        return SessionOutcome(session_id, arm, True, base_order_id, True, True, None)
    upsell_order_id, upsell_total_minor = upsell

    async with UnitOfWork(session_factory) as uow:
        await emit_order_completed(
            uow,
            session_id=session_id,
            arm=arm,
            order_id=upsell_order_id,
            total_minor=upsell_total_minor,
            currency="INR",
        )
        await uow.commit()

    return SessionOutcome(session_id, arm, True, base_order_id, True, True, upsell_order_id)


async def run_growth_simulation(
    session_factory: async_sessionmaker[AsyncSession],
    provider: SimulatorAdapter,
    clock: Clock,
    breaker: CircuitBreaker,
    *,
    seed: str,
    sessions: int,
) -> list[SessionOutcome]:
    """§28 P8 instruction 9: runs exactly `sessions` seeded baseline
    sessions and `sessions` seeded upsell sessions. Never contacts
    Razorpay (the caller must pass a `SimulatorAdapter`) or Groq (nothing
    in this module ever constructs or calls an `LLMClient`)."""
    await _seed_catalog(session_factory)
    outcomes: list[SessionOutcome] = []
    for i in range(sessions):
        outcomes.append(
            await run_session(
                session_factory, provider, clock, breaker, seed=seed, index=i, arm=ARM_BASELINE
            )
        )
    for i in range(sessions):
        outcomes.append(
            await run_session(
                session_factory, provider, clock, breaker, seed=seed, index=i, arm=ARM_UPSELL
            )
        )
    return outcomes
