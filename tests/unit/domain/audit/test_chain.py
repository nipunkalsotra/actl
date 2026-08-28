import hashlib

import pytest

from actl.domain.audit.canonical import jcs
from actl.domain.audit.chain import (
    GENESIS_PREV_HASH,
    compute_entry_hash,
    hex_prefixed,
    parse_hex_prefixed,
    payload_hash,
)


def test_genesis_prev_hash_is_32_zero_bytes() -> None:
    assert GENESIS_PREV_HASH == b"\x00" * 32
    assert len(GENESIS_PREV_HASH) == 32


def test_payload_hash_matches_sha256_of_jcs() -> None:
    payload = {"b": 1, "a": [2, 3]}
    expected = hashlib.sha256(jcs(payload).encode("utf-8")).digest()
    assert payload_hash(payload) == expected


def test_entry_hash_formula_matches_16_1() -> None:
    """entry_hash = sha256(prev_hash_bytes || sha256(jcs(payload)))."""
    prev = hashlib.sha256(b"anything").digest()
    payload = {"amount_minor": 840000, "currency": "INR"}
    expected = hashlib.sha256(prev + payload_hash(payload)).digest()
    assert compute_entry_hash(prev, payload) == expected


def test_entry_hash_is_deterministic() -> None:
    prev = GENESIS_PREV_HASH
    payload = {"x": 1}
    assert compute_entry_hash(prev, payload) == compute_entry_hash(prev, payload)


def test_entry_hash_changes_with_payload() -> None:
    prev = GENESIS_PREV_HASH
    assert compute_entry_hash(prev, {"x": 1}) != compute_entry_hash(prev, {"x": 2})


def test_entry_hash_changes_with_prev_hash() -> None:
    payload = {"x": 1}
    a = compute_entry_hash(GENESIS_PREV_HASH, payload)
    b = compute_entry_hash(hashlib.sha256(b"other").digest(), payload)
    assert a != b


def test_entry_hash_rejects_wrong_length_prev_hash() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        compute_entry_hash(b"too-short", {"x": 1})


def test_hex_prefixed_round_trips() -> None:
    digest = compute_entry_hash(GENESIS_PREV_HASH, {"x": 1})
    encoded = hex_prefixed(digest)
    assert encoded.startswith("sha256:")
    assert len(encoded) == len("sha256:") + 64
    assert parse_hex_prefixed(encoded) == digest


def test_parse_hex_prefixed_rejects_missing_prefix() -> None:
    with pytest.raises(ValueError, match="sha256:"):
        parse_hex_prefixed("deadbeef")


def test_parse_hex_prefixed_rejects_wrong_length() -> None:
    with pytest.raises(ValueError, match="32-byte"):
        parse_hex_prefixed("sha256:deadbeef")
