"""§28 P4: the demo-only admin price-mutation endpoint and the stale-price
scenario it exists to trigger. G5's actual STALE_PRICE gate check is P6
scope (§28 P4 instruction says "do not perform payment, order creation,
capture") -- what P4 must prove is that the drift becomes *detectable*: an
existing quote's pinned price/catalog_version stays untouched while a
fresh catalog read shows the new ones, per §13.2's "converting 'the price
might change' from a silent bug into a detectable, testable condition."
"""

from __future__ import annotations

from tests.integration.catalog.conftest import CatalogTestClient, make_catalog_item
from tests.integration.db.conftest import make_locked_mandate

ADMIN_HEADERS = {"Authorization": "Bearer demo-admin-token-change-me"}


def test_stale_price_scenario_is_detectable_after_admin_mutation(
    client: CatalogTestClient,
) -> None:
    mandate = make_locked_mandate()
    client.seed_mandate(mandate)
    client.seed_items([make_catalog_item("ADM-STALE-01", unit_price_minor=280000)])

    quote_resp = client.http.post(
        "/agent/v1/quote",
        json={"sku": "ADM-STALE-01", "mandate_id": mandate.mandate_id, "nights": 3},
    )
    assert quote_resp.status_code == 201, quote_resp.text
    quote = quote_resp.json()
    assert quote["unit_price_minor"] == 280000
    pinned_version = quote["catalog_version"]

    mutate_resp = client.http.post(
        "/admin/catalog/ADM-STALE-01/price",
        headers=ADMIN_HEADERS,
        json={"unit_price_minor": 350000},
    )
    assert mutate_resp.status_code == 200, mutate_resp.text
    assert mutate_resp.json()["demo_only"] is True

    fresh = client.http.get("/agent/v1/catalog?category=travel.hotel").json()
    fresh_item = next(i for i in fresh["items"] if i["sku"] == "ADM-STALE-01")

    # The quote is immutable: it still carries the price/version pinned at
    # issuance time, even though the live catalog has moved on.
    assert quote["unit_price_minor"] == 280000
    assert quote["catalog_version"] == pinned_version
    # The live feed has moved on -- the drift is now mechanically detectable
    # by comparing the two, exactly what a later gate (P6's G5) would check.
    assert fresh_item["unit_price_minor"] == 350000
    assert fresh_item["version"] > pinned_version
    assert fresh["catalog_version"] > pinned_version


def test_admin_endpoint_requires_correct_token(client: CatalogTestClient) -> None:
    client.seed_items([make_catalog_item("ADM-AUTH-01", unit_price_minor=100000)])

    no_token = client.http.post(
        "/admin/catalog/ADM-AUTH-01/price", json={"unit_price_minor": 120000}
    )
    assert no_token.status_code == 401

    wrong_token = client.http.post(
        "/admin/catalog/ADM-AUTH-01/price",
        headers={"Authorization": "Bearer not-the-token"},
        json={"unit_price_minor": 120000},
    )
    assert wrong_token.status_code == 401


def test_admin_endpoint_rejects_unknown_sku(client: CatalogTestClient) -> None:
    resp = client.http.post(
        "/admin/catalog/DOES-NOT-EXIST/price",
        headers=ADMIN_HEADERS,
        json={"unit_price_minor": 100000},
    )
    assert resp.status_code == 404


def test_admin_endpoint_rejects_non_positive_price(client: CatalogTestClient) -> None:
    client.seed_items([make_catalog_item("ADM-BADPRICE-01", unit_price_minor=100000)])

    resp = client.http.post(
        "/admin/catalog/ADM-BADPRICE-01/price",
        headers=ADMIN_HEADERS,
        json={"unit_price_minor": 0},
    )
    assert resp.status_code == 422
