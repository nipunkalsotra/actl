"""§28 P12: the thin buyer-facing REST surface (`interfaces/http/routers/
buyer.py`) exercised end to end over a real Postgres + SimulatorAdapter +
NullLLMClient -- the same TestClient-background-loop precedent
`tests/integration/agents/conftest.py` already documents (ADR 0005
decision 12). No gate/saga/policy/audit rule is re-tested here (that's
already covered by tests/integration/{agents,gate,payments,catalog}); this
file only proves the new routes reach those real services correctly."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from tests.integration.catalog.conftest import make_catalog_item

from actl.infrastructure.db.repositories.catalog import CatalogItemRecord
from actl.infrastructure.db.uow import UnitOfWork
from actl.infrastructure.llm.fallback import NullLLMClient
from actl.infrastructure.providers.simulator.adapter import SimulatorAdapter
from actl.interfaces.http.deps import (
    get_llm_client,
    get_payment_provider,
    get_session_factory,
    get_uow,
)
from actl.main import app
from actl.platform.clock import SystemClock


@dataclass
class BuyerClient:
    http: TestClient
    session_factory: async_sessionmaker[AsyncSession]

    def seed(self, *items: CatalogItemRecord) -> None:
        """Stamps each item's `version` to the *live* global catalog
        counter before inserting -- this shared-session Postgres container
        may already have an advanced counter from an earlier stale-price
        test in the same run (same mechanism as `application.demo.
        _seed_item`); a hardcoded `version=1` would make gate G5 see a
        freshly-seeded item as already stale."""
        assert self.http.portal is not None

        async def _do() -> None:
            async with UnitOfWork(self.session_factory) as uow:
                current_version = await uow.catalog.current_version()
                for item in items:
                    await uow.catalog.upsert_item(replace(item, version=current_version))
                await uow.commit()

        self.http.portal.call(_do)


@pytest.fixture
def buyer_client(postgres_url: str) -> Iterator[BuyerClient]:
    test_engine = create_async_engine(postgres_url, pool_size=5, max_overflow=10)
    test_session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    simulator = SimulatorAdapter(clock=SystemClock())

    async def _override_get_uow() -> AsyncIterator[UnitOfWork]:
        async with UnitOfWork(test_session_factory) as uow:
            yield uow

    # `get_uow` (used by create_mandate/get_buyer_catalog/get_order/
    # buyer_explain) builds its own `UnitOfWork()` internally and never
    # goes through the `get_session_factory` dependency at all -- same
    # precedent as tests/integration/catalog/conftest.py's `client`
    # fixture -- so both overrides are needed: this one for `get_uow`
    # routes, `get_session_factory` below for the propose/checkout routes
    # that take a session_factory directly (mirroring the saga/gate call
    # shape `application.demo` and the agent envelope route already use).
    app.dependency_overrides[get_uow] = _override_get_uow
    app.dependency_overrides[get_session_factory] = lambda: test_session_factory
    app.dependency_overrides[get_payment_provider] = lambda: simulator
    app.dependency_overrides[get_llm_client] = lambda: NullLLMClient()
    try:
        with TestClient(app) as http_client:
            yield BuyerClient(http=http_client, session_factory=test_session_factory)
    finally:
        app.dependency_overrides.pop(get_uow, None)
        app.dependency_overrides.pop(get_session_factory, None)
        app.dependency_overrides.pop(get_payment_provider, None)
        app.dependency_overrides.pop(get_llm_client, None)


def test_mandate_extract_missing_budget_never_invents_one(buyer_client: BuyerClient) -> None:
    resp = buyer_client.http.post(
        "/buyer/v1/mandate/extract", json={"conversation_text": "Book me something nice in Goa."}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "clarification_needed"
    assert "max_total_minor" in body["missing_slots"]


def test_create_mandate_locks_and_signs(buyer_client: BuyerClient) -> None:
    resp = buyer_client.http.post(
        "/buyer/v1/mandate",
        json={
            "nights": 2,
            "rooms": 1,
            "max_total_minor": 600000,
            "require_refundable": True,
            "check_in": "2026-09-20",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "LOCKED"
    assert body["mandate_id"].startswith("mdt_")
    assert body["bounds"]["max_unit_minor"] == 300000  # 600000 // 2 nights


def test_catalog_ranked_by_mandate_filters_out_of_cap_items(buyer_client: BuyerClient) -> None:
    buyer_client.seed(
        make_catalog_item("HTL-BUYER-AFFORD", unit_price_minor=100000, rating=4.0),
        make_catalog_item("HTL-BUYER-TOOPRICEY", unit_price_minor=900000, rating=5.0),
    )
    mandate = buyer_client.http.post(
        "/buyer/v1/mandate",
        json={
            "nights": 1,
            "rooms": 1,
            "max_total_minor": 200000,
            "require_refundable": True,
            "check_in": "2026-09-20",
        },
    ).json()

    resp = buyer_client.http.get(f"/buyer/v1/catalog?mandate_id={mandate['mandate_id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ranked"] is True
    skus = [item["sku"] for item in body["items"]]
    assert "HTL-BUYER-AFFORD" in skus
    assert "HTL-BUYER-TOOPRICEY" not in skus


def test_full_purchase_happy_path_via_buyer_routes(buyer_client: BuyerClient) -> None:
    buyer_client.seed(make_catalog_item("HTL-BUYER-HAPPY", unit_price_minor=250000, rating=4.2))
    mandate = buyer_client.http.post(
        "/buyer/v1/mandate",
        json={
            "nights": 2,
            "rooms": 1,
            "max_total_minor": 900000,
            "require_refundable": True,
            "check_in": "2026-09-20",
        },
    ).json()

    quote = buyer_client.http.post(
        "/agent/v1/quote",
        json={"sku": "HTL-BUYER-HAPPY", "mandate_id": mandate["mandate_id"], "nights": 2},
    ).json()

    propose = buyer_client.http.post(
        "/buyer/v1/order/propose",
        json={"quote_id": quote["quote_id"], "mandate_id": mandate["mandate_id"]},
    ).json()
    assert propose["decision"] == "accept"

    checkout = buyer_client.http.post(
        "/buyer/v1/checkout",
        json={"order_id": propose["order_id"], "saga_id": propose["saga_id"]},
    ).json()
    assert checkout["status"] == "COMPLETED"

    status = buyer_client.http.get(f"/buyer/v1/order/{propose['order_id']}").json()
    assert status["status"] == "CAPTURED"

    explain = buyer_client.http.get(f"/buyer/v1/audit/explain/{propose['order_id']}")
    assert explain.status_code == 200
    explain_body = explain.json()
    assert explain_body["terminal_outcome"]["status"] == "CAPTURED"
    assert len(explain_body["timeline"]) > 0
    # never a secret in a buyer-facing response, mirroring the reviewer
    # explain route's own guarantee (README "Security notes").
    raw = explain.text
    assert "mandate_signing_key" not in raw
    assert "quote_signing_key" not in raw


def test_over_cap_order_is_denied_without_reservation(buyer_client: BuyerClient) -> None:
    buyer_client.seed(make_catalog_item("HTL-BUYER-OVERCAP", unit_price_minor=550000, rating=4.9))

    # A quote can still be issued (P4 scope never checks mandate bounds);
    # the cap is only enforced at propose time by the real policy engine.
    loose_mandate = buyer_client.http.post(
        "/buyer/v1/mandate",
        json={
            "nights": 1,
            "rooms": 1,
            "max_total_minor": 900000,
            "require_refundable": False,
            "check_in": "2026-09-20",
        },
    ).json()
    assert loose_mandate["bounds"]["max_unit_minor"] == 900000
    buyer_client.http.post(
        "/agent/v1/quote",
        json={
            "sku": "HTL-BUYER-OVERCAP",
            "mandate_id": loose_mandate["mandate_id"],
            "nights": 1,
        },
    )

    # A tighter mandate whose own max_unit_minor (300000) the unit price
    # (550000) actually violates.
    tight_mandate = buyer_client.http.post(
        "/buyer/v1/mandate",
        json={
            "nights": 1,
            "rooms": 1,
            "max_total_minor": 300000,
            "require_refundable": False,
            "check_in": "2026-09-20",
        },
    ).json()
    tight_quote = buyer_client.http.post(
        "/agent/v1/quote",
        json={
            "sku": "HTL-BUYER-OVERCAP",
            "mandate_id": tight_mandate["mandate_id"],
            "nights": 1,
        },
    ).json()

    propose = buyer_client.http.post(
        "/buyer/v1/order/propose",
        json={"quote_id": tight_quote["quote_id"], "mandate_id": tight_mandate["mandate_id"]},
    ).json()
    assert propose["decision"] == "reject"
    assert propose["reason_code"] == "UNIT_CAP_EXCEEDED"
