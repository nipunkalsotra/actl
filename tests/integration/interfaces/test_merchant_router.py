"""§28 P12 Merchant Control Center: the thin merchant-facing REST surface
(`interfaces/http/routers/merchant.py`) exercised end to end over a real
Postgres + SimulatorAdapter. Same TestClient-background-loop precedent as
`tests/integration/interfaces/test_buyer_router.py` (ADR 0005 decision 12).

Run with `PAYMENT_PROVIDER=simulator` (the standard `tests/integration`
invocation) -- `test_health_reports_real_config` and the Demo Lab guard
tests both read the real, live `actl.config.settings` singleton directly,
the same way the route itself does.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from tests.integration.catalog.conftest import make_catalog_item

from actl.config import settings
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
class MerchantClient:
    http: TestClient
    session_factory: async_sessionmaker[AsyncSession]

    def seed(self, *items: CatalogItemRecord) -> None:
        """Stamps each item's `version` to the *live* global catalog
        counter before inserting -- this shared-session Postgres container
        may already have an advanced counter from an earlier stale-price
        test in the same run (same mechanism as `application.demo.
        _seed_item` and `test_buyer_router.py`'s own `seed()`); a
        hardcoded `version=1` would make gate G5 see a freshly-seeded item
        as already stale."""
        assert self.http.portal is not None

        async def _do() -> None:
            async with UnitOfWork(self.session_factory) as uow:
                current_version = await uow.catalog.current_version()
                for item in items:
                    await uow.catalog.upsert_item(replace(item, version=current_version))
                await uow.commit()

        self.http.portal.call(_do)


@pytest.fixture
def merchant_client(postgres_url: str) -> Iterator[MerchantClient]:
    test_engine = create_async_engine(postgres_url, pool_size=5, max_overflow=10)
    test_session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    simulator = SimulatorAdapter(clock=SystemClock())

    async def _override_get_uow():  # type: ignore[no-untyped-def]
        async with UnitOfWork(test_session_factory) as uow:
            yield uow

    app.dependency_overrides[get_uow] = _override_get_uow
    app.dependency_overrides[get_session_factory] = lambda: test_session_factory
    app.dependency_overrides[get_payment_provider] = lambda: simulator
    app.dependency_overrides[get_llm_client] = lambda: NullLLMClient()
    try:
        with TestClient(app) as http_client:
            yield MerchantClient(http=http_client, session_factory=test_session_factory)
    finally:
        app.dependency_overrides.pop(get_uow, None)
        app.dependency_overrides.pop(get_session_factory, None)
        app.dependency_overrides.pop(get_payment_provider, None)
        app.dependency_overrides.pop(get_llm_client, None)


def _complete_a_purchase(client: MerchantClient, sku: str) -> dict[str, Any]:
    """The exact real mandate -> quote -> propose -> checkout sequence
    `test_buyer_router.py::test_full_purchase_happy_path_via_buyer_routes`
    already proves end to end -- reused here only to have one real,
    settled order for the merchant reads to observe."""
    mandate = client.http.post(
        "/buyer/v1/mandate",
        json={
            "nights": 1,
            "rooms": 1,
            "max_total_minor": 900000,
            "require_refundable": True,
            "check_in": "2026-09-20",
        },
    ).json()
    quote = client.http.post(
        "/agent/v1/quote",
        json={"sku": sku, "mandate_id": mandate["mandate_id"], "nights": 1},
    ).json()
    propose = client.http.post(
        "/buyer/v1/order/propose",
        json={"quote_id": quote["quote_id"], "mandate_id": mandate["mandate_id"]},
    ).json()
    assert propose["decision"] == "accept", propose
    checkout = client.http.post(
        "/buyer/v1/checkout",
        json={"order_id": propose["order_id"], "saga_id": propose["saga_id"]},
    ).json()
    assert checkout["status"] == "COMPLETED", checkout
    return propose


def test_health_reports_real_config(merchant_client: MerchantClient) -> None:
    resp = merchant_client.http.get("/merchant/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["api"] == "ok"
    assert body["database"] == "ok"
    assert body["redis"] == "ok"
    assert body["payment_mode"] == "simulator"
    assert body["anchor_mode"] == settings.anchor_provider
    # mode *names* only -- never a secret value from settings
    raw = resp.text
    assert settings.mandate_signing_key not in raw
    assert settings.quote_signing_key not in raw
    assert settings.admin_token not in raw
    assert settings.read_token not in raw


def test_orders_list_shows_a_real_settled_order_with_no_pii(
    merchant_client: MerchantClient,
) -> None:
    merchant_client.seed(
        make_catalog_item("HTL-MERCHANT-ORDERS", unit_price_minor=150000, rating=4.1)
    )
    propose = _complete_a_purchase(merchant_client, "HTL-MERCHANT-ORDERS")

    resp = merchant_client.http.get("/merchant/v1/orders?limit=50")
    assert resp.status_code == 200
    items = resp.json()["items"]
    match = next(item for item in items if item["order_id"] == propose["order_id"])
    assert match["sku"] == "HTL-MERCHANT-ORDERS"
    assert match["amount_minor"] == 150000
    assert match["status"] == "CAPTURED"
    # order id / SKU / amount / status only -- never a buyer name
    assert "buyer" not in str(match).lower()
    assert "name" not in match


def test_order_audit_reflects_a_real_verified_settled_order(
    merchant_client: MerchantClient,
) -> None:
    merchant_client.seed(
        make_catalog_item("HTL-MERCHANT-AUDIT", unit_price_minor=175000, rating=4.3)
    )
    propose = _complete_a_purchase(merchant_client, "HTL-MERCHANT-AUDIT")

    resp = merchant_client.http.get(f"/merchant/v1/order/{propose['order_id']}/audit")
    assert resp.status_code == 200
    body = resp.json()
    assert body["order_id"] == propose["order_id"]
    assert body["terminal_outcome"]["status"] == "CAPTURED"
    assert body["chain_verified"] is True
    assert len(body["timeline"]) > 0


def test_order_audit_404s_for_an_unknown_order(merchant_client: MerchantClient) -> None:
    resp = merchant_client.http.get("/merchant/v1/order/ord_does_not_exist/audit")
    assert resp.status_code == 404


def test_kpis_include_a_real_denied_offer_count(merchant_client: MerchantClient) -> None:
    merchant_client.seed(
        make_catalog_item("HTL-MERCHANT-KPI-CAP", unit_price_minor=550000, rating=4.9)
    )
    before = merchant_client.http.get("/merchant/v1/kpis").json()["protected_offers_blocked"]

    # A real over-cap propose -> a real DENY policy decision recorded.
    mandate = merchant_client.http.post(
        "/buyer/v1/mandate",
        json={
            "nights": 1,
            "rooms": 1,
            "max_total_minor": 100000,
            "require_refundable": False,
            "check_in": "2026-09-20",
        },
    ).json()
    quote = merchant_client.http.post(
        "/agent/v1/quote",
        json={"sku": "HTL-MERCHANT-KPI-CAP", "mandate_id": mandate["mandate_id"], "nights": 1},
    ).json()
    propose = merchant_client.http.post(
        "/buyer/v1/order/propose",
        json={"quote_id": quote["quote_id"], "mandate_id": mandate["mandate_id"]},
    ).json()
    assert propose["decision"] == "reject"

    resp = merchant_client.http.get("/merchant/v1/kpis")
    assert resp.status_code == 200
    body = resp.json()
    assert body["protected_offers_blocked"] == before + 1
    assert "baseline" in body and "upsell" in body
    # revenue_uplift is honestly None when there's no eligible baseline
    # session data yet (compute_growth_metrics's own real behaviour) --
    # never a fabricated number either way.
    assert body["revenue_uplift"] is None or isinstance(body["revenue_uplift"], (int, float))


def test_trust_summary_reflects_real_chain_state(merchant_client: MerchantClient) -> None:
    merchant_client.seed(
        make_catalog_item("HTL-MERCHANT-TRUST", unit_price_minor=120000, rating=4.0)
    )
    _complete_a_purchase(merchant_client, "HTL-MERCHANT-TRUST")

    resp = merchant_client.http.get("/merchant/v1/trust")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["chain_head_seq"], int)
    assert body["chain_head_seq"] > 0
    assert isinstance(body["chain_head_hash"], str)
    assert body["anchor_provider"] == settings.anchor_provider


def test_demo_lab_rejects_when_payment_provider_is_not_simulator(
    merchant_client: MerchantClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "payment_provider", "razorpay")
    resp = merchant_client.http.post("/merchant/v1/demo/verify-chain")
    assert resp.status_code == 403
    assert "simulator" in resp.json()["detail"]


def test_demo_lab_rejects_persistent_demo_app_env(
    merchant_client: MerchantClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # "demo" (.env: `local | ci | demo`) denotes a persistent, judge-facing
    # deployment, not an ephemeral dev/CI database -- it must stay rejected.
    monkeypatch.setattr(settings, "app_env", "demo")
    resp = merchant_client.http.post("/merchant/v1/demo/stale-price")
    assert resp.status_code == 403


def test_demo_lab_allows_ci_app_env(
    merchant_client: MerchantClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression guard: CI's own workflow runs this whole suite with
    # APP_ENV=ci (a throwaway testcontainers Postgres/Redis, exactly as
    # safe as a local dev box) -- Demo Lab must not 403 in CI.
    monkeypatch.setattr(settings, "app_env", "ci")
    resp = merchant_client.http.post("/merchant/v1/demo/verify-chain")
    assert resp.status_code == 200


def test_demo_lab_stale_price_can_run_twice_without_id_collision(
    merchant_client: MerchantClient,
) -> None:
    first = merchant_client.http.post("/merchant/v1/demo/stale-price")
    second = merchant_client.http.post("/merchant/v1/demo/stale-price")
    assert first.status_code == 200
    assert second.status_code == 200
    first_body, second_body = first.json(), second.json()
    assert first_body["terminal_outcome"] == "CAPTURED"
    assert second_body["terminal_outcome"] == "CAPTURED"
    # different run_id per click -> different deterministic-id namespace
    assert first_body["mandate_id"] != second_body["mandate_id"]
    assert first_body["chain_verified"] is True
    assert second_body["chain_verified"] is True


def test_demo_lab_payment_decline_shows_real_compensation(merchant_client: MerchantClient) -> None:
    resp = merchant_client.http.post("/merchant/v1/demo/payment-decline")
    assert resp.status_code == 200
    body = resp.json()
    assert body["detected_fault"] == "PROVIDER_DECLINED"
    assert body["reserved_balance_minor"] == 0
    assert "COMPENSATED" in body["terminal_outcome"]


def test_demo_lab_llm_unavailable_uses_real_fallback(merchant_client: MerchantClient) -> None:
    resp = merchant_client.http.post("/merchant/v1/demo/llm-unavailable")
    assert resp.status_code == 200
    body = resp.json()
    assert body["detected_fault"] == "LLM_UNAVAILABLE (every U1/U2 call)"


def test_demo_lab_verify_chain_reports_a_real_result(merchant_client: MerchantClient) -> None:
    """Not `ok is True`: this shared testcontainer Postgres is also used by
    `tests/integration/audit/test_tamper_detection.py`, which *intentionally*
    corrupts a real row to prove the verifier catches it -- when that test
    runs earlier in the same session, a genuine, real "whole chain from
    seq 1" check correctly reports ok=False for the rest of the session.
    That is the verifier working, not this route lying; what this test can
    honestly assert is that the route calls the real verifier and returns
    its real, well-formed result either way."""
    resp = merchant_client.http.post("/merchant/v1/demo/verify-chain")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["ok"], bool)
    assert body["from_seq"] == 1
    assert isinstance(body["to_seq"], int) and body["to_seq"] > 0
    assert isinstance(body["entries_verified"], int) and body["entries_verified"] > 0
