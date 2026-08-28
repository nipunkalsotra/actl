"""§28 P4 exit criteria: test_version_bumps_on_price_change,
test_feed_contains_no_free_text_fields, plus paging/ordering/ETag coverage
(§28 P4 instruction 7)."""

from __future__ import annotations

from typing import Any

from tests.integration.catalog.conftest import CatalogTestClient, make_catalog_item

ADMIN_HEADERS = {"Authorization": "Bearer demo-admin-token-change-me"}


def _walk_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            keys.add(k)
            keys |= _walk_keys(v)
    elif isinstance(value, list):
        for item in value:
            keys |= _walk_keys(item)
    return keys


def test_version_bumps_on_price_change(client: CatalogTestClient) -> None:
    client.seed_items([make_catalog_item("CAT-VER-01", unit_price_minor=100000)])

    before = client.http.get("/agent/v1/catalog?category=travel.hotel").json()
    version_before = before["catalog_version"]

    resp = client.http.post(
        "/admin/catalog/CAT-VER-01/price",
        headers=ADMIN_HEADERS,
        json={"unit_price_minor": 150000},
    )
    assert resp.status_code == 200, resp.text

    after = client.http.get("/agent/v1/catalog?category=travel.hotel").json()
    assert after["catalog_version"] == version_before + 1

    item = next(i for i in after["items"] if i["sku"] == "CAT-VER-01")
    assert item["unit_price_minor"] == 150000
    assert item["version"] == after["catalog_version"]


def test_feed_contains_no_free_text_fields(client: CatalogTestClient) -> None:
    client.seed_items([make_catalog_item("CAT-NOTEXT-01", unit_price_minor=200000)])

    body = client.http.get("/agent/v1/catalog?category=travel.hotel").json()
    keys = _walk_keys(body)

    forbidden = {"description", "summary", "notes", "about", "blurb", "copy", "text"}
    leaked = keys & forbidden
    assert not leaked, f"free-text-looking keys leaked into the feed: {leaked}"


def test_catalog_paging_is_stable_and_gapless(client: CatalogTestClient) -> None:
    skus = [f"CAT-PAGE-{i:02d}" for i in range(5)]
    client.seed_items(
        [make_catalog_item(sku, unit_price_minor=100000 + i * 1000) for i, sku in enumerate(skus)]
    )

    seen: list[str] = []
    cursor: str | None = None
    # Generous upper bound: other tests in this module share the same
    # container-backed Postgres instance and add their own items, so the
    # full walk may need to cross many pages before reaching this test's
    # five skus. A pagination bug (duplicate/skip/infinite loop) should
    # still surface well before this many iterations.
    for _ in range(100):
        params: dict[str, Any] = {"category": "travel.hotel", "limit": 2}
        if cursor is not None:
            params["cursor"] = cursor
        body = client.http.get("/agent/v1/catalog", params=params).json()
        seen.extend(item["sku"] for item in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break

    our_items = [sku for sku in seen if sku in skus]
    assert our_items == skus  # ascending price order, no dup, no skip
    assert len(seen) == len(set(seen))  # no duplicates across the whole walk


def test_catalog_etag_matches_and_304_on_conditional_get(client: CatalogTestClient) -> None:
    client.seed_items([make_catalog_item("CAT-ETAG-01", unit_price_minor=100000)])

    first = client.http.get("/agent/v1/catalog?category=travel.hotel")
    etag = first.headers["etag"]
    assert etag.startswith('"cat-v')
    assert first.headers["cache-control"] == "max-age=30"

    second = client.http.get(
        "/agent/v1/catalog?category=travel.hotel", headers={"If-None-Match": etag}
    )
    assert second.status_code == 304
    assert second.headers["etag"] == etag
    assert second.content == b""


def test_catalog_etag_changes_when_query_differs_at_same_version(
    client: CatalogTestClient,
) -> None:
    client.seed_items([make_catalog_item("CAT-ETAG-02", unit_price_minor=100000)])

    a = client.http.get("/agent/v1/catalog?category=travel.hotel")
    b = client.http.get("/agent/v1/catalog?category=travel.hotel&max_unit_minor=50000")
    assert a.headers["etag"] != b.headers["etag"]


def test_catalog_filters_by_category_location_and_max_price(client: CatalogTestClient) -> None:
    client.seed_items(
        [
            make_catalog_item(
                "CAT-FILT-CHEAP", unit_price_minor=90000, location_city="Goa", location_country="IN"
            ),
            make_catalog_item(
                "CAT-FILT-PRICEY",
                unit_price_minor=900000,
                location_city="Goa",
                location_country="IN",
            ),
            make_catalog_item(
                "CAT-FILT-ELSEWHERE",
                unit_price_minor=90000,
                location_city="Mumbai",
                location_country="IN",
            ),
        ]
    )

    body = client.http.get(
        "/agent/v1/catalog",
        params={"category": "travel.hotel", "location": "Goa,IN", "max_unit_minor": 300000},
    ).json()
    skus = {item["sku"] for item in body["items"]}
    assert "CAT-FILT-CHEAP" in skus
    assert "CAT-FILT-PRICEY" not in skus
    assert "CAT-FILT-ELSEWHERE" not in skus
