"""§28 P4 instruction 6: JSON Schema contract tests for docs/protocol/.
Pure -- no database, no HTTP server. Response shapes are proven against
representative instances built straight from the domain models (the same
models the real routers serialize), and the well-known document is the
router's own static dict -- not a second, hand-copied example that could
drift from what either module actually produces.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from actl.domain.catalog.models import (
    CatalogAttributes,
    CatalogFeed,
    CatalogItem,
    CatalogLocation,
    CatalogPolicy,
)
from actl.domain.catalog.quote import Quote, build_quote_token, compute_quote_hash
from actl.interfaces.http.routers.well_known import _DOCUMENT as WELL_KNOWN_DOCUMENT

_PROTOCOL_DIR = Path(__file__).resolve().parents[2] / "docs" / "protocol"


def _load_schema(name: str) -> dict[str, Any]:
    return json.loads((_PROTOCOL_DIR / name).read_text())


def _example_catalog_item(sku: str = "HTL-GOA-SEA-DLX") -> CatalogItem:
    return CatalogItem(
        sku=sku,
        category="travel.hotel",
        merchant_id="mrc_seabreeze",
        unit="night",
        unit_price_minor=280000,
        available_units=6,
        location=CatalogLocation(city="Goa", country="IN"),
        attributes=CatalogAttributes(rating=4.4, sea_facing=True, breakfast_included=True),
        policy=CatalogPolicy(
            refundable=True, cancellation_window_h=48, instant_confirm=True, taxes_included=True
        ),
        version=118,
        quote_required=True,
    )


def _example_catalog_feed() -> CatalogFeed:
    return CatalogFeed(
        catalog_version=118,
        generated_at=datetime(2026, 8, 28, 9, 3, 58, tzinfo=UTC),
        items=[_example_catalog_item()],
        next_cursor=None,
    )


def _example_quote() -> Quote:
    draft = Quote(
        quote_id="qte_01JX8Z70A",
        sku="HTL-GOA-SEA-DLX",
        mandate_id="mdt_01JX8Z6QK4T2N9V0",
        unit_price_minor=280000,
        nights=3,
        total_minor=840000,
        catalog_version=118,
        refundable=True,
        expires_at=datetime(2026, 8, 28, 9, 6, 11, tzinfo=UTC),
    )
    quote_hash = compute_quote_hash(draft)
    token = build_quote_token(draft, quote_hash, b"contract-test-key")
    return draft.model_copy(update={"quote_hash": quote_hash, "quote_token": token})


def test_catalog_response_validates_against_schema() -> None:
    schema = _load_schema("catalog.schema.json")
    body = _example_catalog_feed().model_dump(mode="json", by_alias=True)
    jsonschema.validate(body, schema)


def test_quote_response_validates_against_schema() -> None:
    schema = _load_schema("quote.schema.json")
    body = _example_quote().model_dump(mode="json", by_alias=True)
    jsonschema.validate(body, schema)


def test_quote_request_validates_against_schema() -> None:
    schema = _load_schema("quote_request.schema.json")
    jsonschema.validate({"sku": "HTL-GOA-SEA-DLX", "mandate_id": "mdt_x", "nights": 3}, schema)

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"sku": "HTL-GOA-SEA-DLX", "mandate_id": "mdt_x"}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"sku": "x", "mandate_id": "mdt_x", "nights": 0}, schema)


def test_well_known_document_matches_its_schema_and_advertised_protocol() -> None:
    schema = _load_schema("agent-commerce.schema.json")
    jsonschema.validate(WELL_KNOWN_DOCUMENT, schema)

    assert WELL_KNOWN_DOCUMENT["protocol"] == "actl.acp/1"
    assert WELL_KNOWN_DOCUMENT["currency"] == "INR"
    assert WELL_KNOWN_DOCUMENT["endpoints"]["catalog"] == "/agent/v1/catalog"
    assert WELL_KNOWN_DOCUMENT["endpoints"]["quote"] == "/agent/v1/quote"
    assert "HMAC-SHA256" in WELL_KNOWN_DOCUMENT["signing"]["algorithms"]


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


def test_no_free_text_field_in_the_catalog_contract() -> None:
    """§13.1: "the feed contains no free-text description field at all."
    Schema-level proof: additionalProperties is false everywhere in
    catalog.schema.json, so an actual instance carrying an extra field
    (e.g. "description") would already fail schema validation above --
    this test asserts the forbidden field names directly, at both the
    schema's declared property set and a real serialized instance."""
    schema = _load_schema("catalog.schema.json")
    item_props = set(schema["$defs"]["catalogItem"]["properties"])
    forbidden = {"description", "summary", "notes", "about", "blurb", "copy"}
    assert item_props.isdisjoint(forbidden)
    assert schema["$defs"]["catalogItem"]["additionalProperties"] is False

    body = _example_catalog_feed().model_dump(mode="json", by_alias=True)
    assert _walk_keys(body).isdisjoint(forbidden)
