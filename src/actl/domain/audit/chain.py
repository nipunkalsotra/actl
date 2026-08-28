"""Hash chain construction (§16.1):
entry_hash = sha256(prev_hash_bytes || sha256(jcs(payload)))
Genesis prev_hash is exactly 32 zero bytes.

Pure — no I/O, no clock, no randomness. This module only computes hashes
from values it's given; the append service (application layer) is what
actually reads the chain tail and writes rows.
"""

from __future__ import annotations

import hashlib

from actl.domain.audit.canonical import JSONValue, jcs

GENESIS_PREV_HASH: bytes = b"\x00" * 32
_HEX_PREFIX = "sha256:"


def payload_hash(payload: JSONValue) -> bytes:
    """sha256(JCS(payload)) — RFC 8785 canonical JSON, per §16.1."""
    return hashlib.sha256(jcs(payload).encode("utf-8")).digest()


def compute_entry_hash(prev_hash: bytes, payload: JSONValue) -> bytes:
    if len(prev_hash) != 32:
        raise ValueError(f"prev_hash must be 32 bytes, got {len(prev_hash)}")
    return hashlib.sha256(prev_hash + payload_hash(payload)).digest()


def hex_prefixed(digest: bytes) -> str:
    """32 raw bytes -> 'sha256:<hex>', the on-the-wire / on-disk form (§8.3)."""
    return f"{_HEX_PREFIX}{digest.hex()}"


def parse_hex_prefixed(value: str) -> bytes:
    if not value.startswith(_HEX_PREFIX):
        raise ValueError(f"expected a {_HEX_PREFIX!r}-prefixed hash, got {value!r}")
    digest = bytes.fromhex(value[len(_HEX_PREFIX) :])
    if len(digest) != 32:
        raise ValueError(f"expected a 32-byte sha256 digest, got {len(digest)} bytes")
    return digest
