"""Shared fixtures for tests/integration/payments. Container/engine/
session fixtures (postgres_url, engine, session_factory) live in the
parent tests/integration/conftest.py, shared across every
tests/integration/* subdirectory."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.domain.mandate.state_machine import MandateStatus
from actl.domain.policy.decision import DecisionRecord
from actl.infrastructure.db.repositories.quotes import QuoteRecord
from actl.infrastructure.db.uow import UnitOfWork
from actl.platform.clock import Clock
from actl.platform.ids import new_id
from tests.integration.db.conftest import make_locked_mandate


def fake_hash(seed: str) -> str:
    """A well-formed `sha256:<64-hex>` string for test fixture fields that
    are typed as hashes but not independently verified — real hex, not a
    placeholder (ADR 0004 decision 5's lesson: malformed hash strings
    break downstream code that parses them as real hashes)."""
    return f"sha256:{hashlib.sha256(seed.encode()).hexdigest()}"


@dataclass(frozen=True)
class PurchaseFixture:
    mandate_id: str
    decision_id: str
    quote_id: str
    intent_hash: str
    amount_minor: int


async def seed_purchase_fixture(
    session_factory: async_sessionmaker[AsyncSession],
    clock: Clock,
    *,
    unit_price_minor: int = 280000,
    nights: int = 3,
) -> PurchaseFixture:
    """Persists a fresh, uniquely-ided locked mandate + ALLOW decision +
    quote — the FK chain `orders` requires (§18.2: orders.mandate_id/
    decision_id/quote_id are all NOT NULL foreign keys)."""
    mandate = make_locked_mandate()
    async with UnitOfWork(session_factory) as uow:
        await uow.mandates.add(mandate, MandateStatus.LOCKED)
        await uow.commit()

    intent_hash = fake_hash(new_id("intent"))
    decision_id = new_id("dec")
    quote_id = new_id("qte")
    async with UnitOfWork(session_factory) as uow:
        await uow.decisions.add(
            DecisionRecord(
                decision_id=decision_id,
                engine_version="v1",
                mandate_id=mandate.mandate_id,
                mandate_spec_hash=mandate.spec_hash,
                intent_hash=intent_hash,
                verdict="ALLOW",
                reason_codes=[],
                rule_trace=[],
                evaluated_at=clock.now(),
                ttl_s=30,
                inputs_digest=fake_hash(new_id("inputs")),
            )
        )
        await uow.quotes.add(
            QuoteRecord(
                id=quote_id,
                mandate_id=mandate.mandate_id,
                sku="HTL-GOA-SEA-DLX",
                unit_price_minor=unit_price_minor,
                nights=nights,
                total_minor=unit_price_minor * nights,
                currency="INR",
                catalog_version=1,
                refundable=True,
                quote_token="qt_v1.x.y",
                quote_hash=fake_hash(new_id("quote")),
                expires_at=clock.now(),
            )
        )
        await uow.commit()

    return PurchaseFixture(
        mandate_id=mandate.mandate_id,
        decision_id=decision_id,
        quote_id=quote_id,
        intent_hash=intent_hash,
        amount_minor=unit_price_minor * nights,
    )
