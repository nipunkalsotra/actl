"""Prefixed ULIDs (§00 conventions): mdt_, ord_, dec_, qte_, agt_, ...

Hand-rolled rather than a dependency: the ULID spec is 26 lines of Crockford
Base32 over a 48-bit timestamp + 80 bits of randomness, and that's the whole
surface area we need.
"""

from __future__ import annotations

import os
import time

_CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ULID_LENGTH = 26
_RANDOM_BITS = 80


def _encode_crockford(value: int, length: int) -> str:
    chars = [""] * length
    for i in range(length - 1, -1, -1):
        chars[i] = _CROCKFORD_ALPHABET[value & 0x1F]
        value >>= 5
    return "".join(chars)


def ulid() -> str:
    """A bare ULID: 48-bit millisecond timestamp || 80 bits of CSPRNG randomness."""
    timestamp_ms = time.time_ns() // 1_000_000
    randomness = int.from_bytes(os.urandom(_RANDOM_BITS // 8), "big")
    value = (timestamp_ms << _RANDOM_BITS) | randomness
    return _encode_crockford(value, _ULID_LENGTH)


def new_id(prefix: str) -> str:
    """A prefixed ULID, e.g. new_id("mdt") -> 'mdt_01JX8Z6QK4T2N9V0...'."""
    return f"{prefix}_{ulid()}"
