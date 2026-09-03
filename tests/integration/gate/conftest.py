"""Shared fixtures for tests/integration/gate (§28 P6). Container/engine/
session fixtures (postgres_url, engine, session_factory) live in the
parent tests/integration/conftest.py, shared across every
tests/integration/* subdirectory.

Deliberately does NOT reuse tests/integration/payments/conftest.py's
seed_purchase_fixture -- that fixture hardcodes catalog_version=1 and
quote.expires_at=clock.now() (already-expired-on-arrival), both fine for
P5's payment tests (which never read either field) but wrong for gate
tests, which specifically exercise G5's freshness check against the real,
live catalog version and a real future expiry.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.domain.mandate.models import Mandate
from actl.domain.mandate.state_machine import MandateStatus
from actl.domain.policy.decision import DecisionRecord
from actl.domain.policy.reason_codes import ReasonCode
from actl.infrastructure.db.repositories.quotes import QuoteRecord
from actl.infrastructure.db.uow import UnitOfWork
from actl.platform.clock import Clock
from actl.platform.ids import new_id
from tests.integration.db.conftest import make_locked_mandate


def fake_hash(seed: str) -> str:
    return f"sha256:{hashlib.sha256(seed.encode()).hexdigest()}"


@dataclass(frozen=True)
class GateFixture:
    mandate: Mandate
    decision_id: str
    quote_id: str
    intent_hash: str
    amount_minor: int


async def seed_mandate(
    session_factory: async_sessionmaker[AsyncSession],
    mandate: Mandate,
    *,
    status: MandateStatus = MandateStatus.LOCKED,
) -> None:
    async with UnitOfWork(session_factory) as uow:
        await uow.mandates.add(mandate, status)
        await uow.commit()


async def seed_decision(
    session_factory: async_sessionmaker[AsyncSession],
    clock: Clock,
    *,
    mandate: Mandate,
    intent_hash: str,
    verdict: str = "ALLOW",
    reason_codes: list[ReasonCode] | None = None,
    evaluated_at: object | None = None,
    ttl_s: int = 30,
) -> str:
    decision_id = new_id("dec")
    async with UnitOfWork(session_factory) as uow:
        await uow.decisions.add(
            DecisionRecord(
                decision_id=decision_id,
                engine_version="v1",
                mandate_id=mandate.mandate_id,
                mandate_spec_hash=mandate.spec_hash or "",
                intent_hash=intent_hash,
                verdict=verdict,  # type: ignore[arg-type]
                reason_codes=reason_codes
                or ([] if verdict == "ALLOW" else [ReasonCode.BUDGET_EXCEEDED]),
                rule_trace=[],
                evaluated_at=evaluated_at or clock.now(),  # type: ignore[arg-type]
                ttl_s=ttl_s,
                inputs_digest=fake_hash(decision_id),
            )
        )
        await uow.commit()
    return decision_id


async def seed_quote(
    session_factory: async_sessionmaker[AsyncSession],
    clock: Clock,
    *,
    mandate_id: str,
    catalog_version: int | None = None,
    expires_in_s: int = 300,
    unit_price_minor: int = 280000,
    nights: int = 3,
) -> str:
    quote_id = new_id("qte")
    async with UnitOfWork(session_factory) as uow:
        if catalog_version is None:
            catalog_version = await uow.catalog.current_version()
        await uow.quotes.add(
            QuoteRecord(
                id=quote_id,
                mandate_id=mandate_id,
                sku="HTL-GOA-SEA-DLX",
                unit_price_minor=unit_price_minor,
                nights=nights,
                total_minor=unit_price_minor * nights,
                currency="INR",
                catalog_version=catalog_version,
                refundable=True,
                quote_token="qt_v1.x.y",
                quote_hash=fake_hash(quote_id),
                expires_at=clock.now() + timedelta(seconds=expires_in_s),
            )
        )
        await uow.commit()
    return quote_id


async def seed_valid_gate_fixture(
    session_factory: async_sessionmaker[AsyncSession],
    clock: Clock,
    *,
    unit_price_minor: int = 280000,
    nights: int = 3,
    mandate: Mandate | None = None,
    decision_ttl_s: int = 30,
) -> GateFixture:
    """A fresh mandate + a fresh ALLOW decision bound to it + a fresh,
    unexpired quote at the *current* live catalog version -- every gate
    (G1-G7) passes on this fixture unmodified. Individual tests mutate
    one field of the returned pieces (or seed their own decision/quote
    with a bad value) to exercise a specific deny path.

    `decision_ttl_s` defaults to `seed_decision`'s own 30s -- a heavy
    concurrency test (tests/chaos/test_f9.py) passes a larger value so its
    own deadlock-retry budget (gate.execute_money_action retries G1-G5 up
    to 50x with up to 0.5s jitter each) can never legitimately push a
    straggler attempt past G2 as DECISION_STALE instead of the G4
    BUDGET_EXCEEDED it's specifically there to prove."""
    mandate = mandate or make_locked_mandate()
    await seed_mandate(session_factory, mandate)

    intent_hash = fake_hash(new_id("intent"))
    decision_id = await seed_decision(
        session_factory,
        clock,
        mandate=mandate,
        intent_hash=intent_hash,
        verdict="ALLOW",
        ttl_s=decision_ttl_s,
    )
    quote_id = await seed_quote(session_factory, clock, mandate_id=mandate.mandate_id)

    return GateFixture(
        mandate=mandate,
        decision_id=decision_id,
        quote_id=quote_id,
        intent_hash=intent_hash,
        amount_minor=unit_price_minor * nights,
    )
