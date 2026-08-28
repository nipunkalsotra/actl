from __future__ import annotations

from datetime import UTC, datetime

import pytest

from actl.domain.catalog.quote import (
    Quote,
    build_quote_token,
    compute_quote_hash,
    parse_and_verify_quote_token,
)

KEY = b"test-quote-signing-key"
OTHER_KEY = b"a-different-key"


def _draft_quote() -> Quote:
    return Quote(
        quote_id="qte_test01",
        sku="HTL-GOA-SEA-DLX",
        mandate_id="mdt_test01",
        unit_price_minor=280000,
        nights=3,
        total_minor=840000,
        catalog_version=118,
        refundable=True,
        expires_at=datetime(2026, 8, 28, 9, 6, 11, tzinfo=UTC),
    )


def test_quote_hash_is_deterministic() -> None:
    quote = _draft_quote()
    assert compute_quote_hash(quote) == compute_quote_hash(quote)


def test_quote_hash_changes_when_price_changes() -> None:
    quote = _draft_quote()
    other = quote.model_copy(update={"unit_price_minor": 300000})
    assert compute_quote_hash(quote) != compute_quote_hash(other)


def test_quote_hash_excludes_quote_token_and_quote_hash_fields() -> None:
    quote = _draft_quote()
    quote_hash = compute_quote_hash(quote)
    signed = quote.model_copy(
        update={"quote_hash": quote_hash, "quote_token": "qt_v1.whatever.sig"}
    )
    assert compute_quote_hash(signed) == quote_hash


def test_quote_token_signature_verifies() -> None:
    quote = _draft_quote()
    quote_hash = compute_quote_hash(quote)
    token = build_quote_token(quote, quote_hash, KEY)

    payload = parse_and_verify_quote_token(token, KEY)

    assert payload["quote_id"] == "qte_test01"
    assert payload["unit_price_minor"] == 280000


def test_quote_token_rejects_wrong_key() -> None:
    quote = _draft_quote()
    quote_hash = compute_quote_hash(quote)
    token = build_quote_token(quote, quote_hash, KEY)

    with pytest.raises(ValueError, match="signature invalid"):
        parse_and_verify_quote_token(token, OTHER_KEY)


def test_quote_token_rejects_tampered_payload() -> None:
    quote = _draft_quote()
    quote_hash = compute_quote_hash(quote)
    token = build_quote_token(quote, quote_hash, KEY)
    prefix, payload_b64, signature = token.split(".")
    tampered = f"{prefix}.{payload_b64}X.{signature}"

    with pytest.raises(ValueError):
        parse_and_verify_quote_token(tampered, KEY)


def test_quote_token_rejects_malformed_shape() -> None:
    with pytest.raises(ValueError, match="malformed"):
        parse_and_verify_quote_token("not-a-token", KEY)

    with pytest.raises(ValueError, match="malformed"):
        parse_and_verify_quote_token("qt_v2.abc.def", KEY)
