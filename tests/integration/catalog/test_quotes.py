"""§28 P4 exit criteria: test_quote_expires_after_ttl,
test_quote_token_signature_verifies, plus price-pinning/malformed-request
coverage (§28 P4 instruction 7)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application.catalog_service import create_quote
from actl.config import settings
from actl.domain.catalog.quote import parse_and_verify_quote_token
from actl.domain.mandate.state_machine import MandateStatus
from actl.infrastructure.db.uow import UnitOfWork
from actl.platform.clock import FrozenClock
from tests.integration.catalog.conftest import CatalogTestClient, make_catalog_item
from tests.integration.db.conftest import make_locked_mandate


def test_quote_token_signature_verifies(client: CatalogTestClient) -> None:
    mandate = make_locked_mandate()
    client.seed_mandate(mandate)
    client.seed_items([make_catalog_item("QTE-SIGN-01", unit_price_minor=200000)])

    resp = client.http.post(
        "/agent/v1/quote",
        json={"sku": "QTE-SIGN-01", "mandate_id": mandate.mandate_id, "nights": 2},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    payload = parse_and_verify_quote_token(
        body["quote_token"], settings.quote_signing_key.encode("utf-8")
    )
    assert payload["quote_id"] == body["quote_id"]
    assert payload["unit_price_minor"] == 200000

    with pytest.raises(ValueError):
        parse_and_verify_quote_token(body["quote_token"], b"wrong-key")


def test_quote_pins_price_and_catalog_version(client: CatalogTestClient) -> None:
    mandate = make_locked_mandate()
    client.seed_mandate(mandate)
    client.seed_items([make_catalog_item("QTE-PIN-01", unit_price_minor=250000)])

    resp = client.http.post(
        "/agent/v1/quote",
        json={"sku": "QTE-PIN-01", "mandate_id": mandate.mandate_id, "nights": 3},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["unit_price_minor"] == 250000
    assert body["total_minor"] == 750000
    assert body["catalog_version"] >= 1
    assert body["currency"] == "INR"


def test_quote_for_untouched_item_pins_live_version_not_stale_item_version(
    client: CatalogTestClient,
) -> None:
    """A quote must pin the live global catalog_version at issuance time,
    not this item's own last-mutated marker -- otherwise an item that's
    never itself been price-mutated would spuriously, permanently look
    STALE_PRICE the instant *any other* item in the catalog is ever
    mutated (e.g. by the demo-only stale-price scenario)."""
    mandate = make_locked_mandate()
    client.seed_mandate(mandate)
    client.seed_items(
        [
            make_catalog_item("QTE-UNTOUCHED-01", unit_price_minor=100000),
            make_catalog_item("QTE-MUTATED-01", unit_price_minor=100000),
        ]
    )

    mutate_resp = client.http.post(
        "/admin/catalog/QTE-MUTATED-01/price",
        headers={"Authorization": "Bearer demo-admin-token-change-me"},
        json={"unit_price_minor": 150000},
    )
    assert mutate_resp.status_code == 200, mutate_resp.text

    quote_resp = client.http.post(
        "/agent/v1/quote",
        json={"sku": "QTE-UNTOUCHED-01", "mandate_id": mandate.mandate_id, "nights": 1},
    )
    assert quote_resp.status_code == 201, quote_resp.text
    quote = quote_resp.json()

    live = client.http.get("/agent/v1/catalog?category=travel.hotel").json()
    assert quote["catalog_version"] == live["catalog_version"]


@pytest.mark.asyncio(loop_scope="session")
async def test_quote_expires_after_ttl(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    mandate = make_locked_mandate()
    async with UnitOfWork(session_factory) as uow:
        await uow.mandates.add(mandate, MandateStatus.LOCKED)
        await uow.catalog.upsert_item(make_catalog_item("QTE-TTL-01", unit_price_minor=180000))
        await uow.commit()

    start = datetime(2026, 1, 1, tzinfo=UTC)
    clock = FrozenClock(at=start)

    with patch.object(settings, "quote_ttl_s", 5):
        async with UnitOfWork(session_factory) as uow:
            quote = await create_quote(
                uow, clock, mandate_id=mandate.mandate_id, sku="QTE-TTL-01", nights=1
            )

    assert quote.expires_at == start + timedelta(seconds=5)

    clock.advance(timedelta(seconds=6))
    assert quote.expires_at < clock.now()  # detectably expired


def test_quote_rejects_unknown_sku(client: CatalogTestClient) -> None:
    mandate = make_locked_mandate()
    client.seed_mandate(mandate)

    resp = client.http.post(
        "/agent/v1/quote",
        json={"sku": "DOES-NOT-EXIST", "mandate_id": mandate.mandate_id, "nights": 1},
    )
    assert resp.status_code == 404


def test_quote_rejects_unknown_mandate(client: CatalogTestClient) -> None:
    client.seed_items([make_catalog_item("QTE-NOMANDATE-01", unit_price_minor=150000)])

    resp = client.http.post(
        "/agent/v1/quote",
        json={"sku": "QTE-NOMANDATE-01", "mandate_id": "mdt_does_not_exist", "nights": 1},
    )
    assert resp.status_code == 404


def test_quote_rejects_sold_out_sku(client: CatalogTestClient) -> None:
    mandate = make_locked_mandate()
    client.seed_mandate(mandate)
    client.seed_items(
        [make_catalog_item("QTE-SOLDOUT-01", unit_price_minor=150000, available_units=0)]
    )

    resp = client.http.post(
        "/agent/v1/quote",
        json={"sku": "QTE-SOLDOUT-01", "mandate_id": mandate.mandate_id, "nights": 1},
    )
    assert resp.status_code == 409


@pytest.mark.parametrize(
    "body",
    [
        {"sku": "", "mandate_id": "mdt_x", "nights": 1},
        {"sku": "X", "mandate_id": "mdt_x", "nights": 0},
        {"sku": "X", "mandate_id": "mdt_x", "nights": -1},
        {"sku": "X", "mandate_id": "mdt_x"},
        {"mandate_id": "mdt_x", "nights": 1},
    ],
)
def test_quote_rejects_malformed_request_body(
    client: CatalogTestClient, body: dict[str, object]
) -> None:
    resp = client.http.post("/agent/v1/quote", json=body)
    assert resp.status_code == 422
