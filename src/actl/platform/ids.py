"""Prefixed ULIDs (§00 conventions): mdt_, ord_, dec_, qte_, agt_, ...

Hand-rolled rather than a dependency: the ULID spec is 26 lines of Crockford
Base32 over a 48-bit timestamp + 80 bits of randomness, and that's the whole
surface area we need.

§28 P9: golden traces need every id in a scenario run to be byte-identical
across reruns, but `create_quote`/`execute_money_action`/`saga.py`/etc. all
call `new_id()` internally with no way to pass an id in from the caller --
threading an id generator through every one of those call sites would be a
large, invasive change to money-critical code for a demo/test-only need.
Instead `ulid()` itself has an opt-in deterministic mode, off by default:
`seed_deterministic_ids()` switches every subsequent `ulid()` call in this
process to a seed+counter-derived value instead of real time + CSPRNG.
Only `actl demo`/golden-trace generation and their own tests ever call it;
normal application code (the HTTP app, the worker, real `actl` money
commands) never does, so this can never activate outside an explicit,
intentional demo/test run.

§28 P10 / §22: a ULID's own payload is exactly 128 bits (48-bit timestamp
+ 80-bit randomness) -- the same width as an OpenTelemetry `TraceId`.
`encode_crockford`/`crockford_decode` are exact inverses of each other,
so `platform.tracing` can losslessly round-trip a `trc_`-prefixed
`trace_id` string to and from a native 128-bit OTel trace id: not two
merely-correlated identifiers, the literal same 128-bit value in two
encodings.
"""

from __future__ import annotations

import hashlib
import os
import time

_CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ULID_LENGTH = 26
_RANDOM_BITS = 80
_ID_BITS = 48 + _RANDOM_BITS

_deterministic_seed: str | None = None
_deterministic_counter: int = 0


def encode_crockford(value: int, length: int) -> str:
    chars = [""] * length
    for i in range(length - 1, -1, -1):
        chars[i] = _CROCKFORD_ALPHABET[value & 0x1F]
        value >>= 5
    return "".join(chars)


def crockford_decode(s: str) -> int:
    """The exact inverse of `encode_crockford`."""
    value = 0
    for ch in s:
        value = (value << 5) | _CROCKFORD_ALPHABET.index(ch)
    return value


def seed_deterministic_ids(seed: str) -> None:
    """Demo/golden-trace-only: every `ulid()` call after this returns a
    value derived from `seed` and an internal call counter, not real
    time/randomness -- the same seed always produces the same sequence of
    ids. Never called by application code that serves real traffic."""
    global _deterministic_seed, _deterministic_counter
    _deterministic_seed = seed
    _deterministic_counter = 0


def reset_ids() -> None:
    """Returns `ulid()` to real time + CSPRNG. Demo/test teardown must
    call this so a deterministic seed never leaks into an unrelated run."""
    global _deterministic_seed, _deterministic_counter
    _deterministic_seed = None
    _deterministic_counter = 0


def ulid() -> str:
    """A bare ULID: 48-bit millisecond timestamp || 80 bits of CSPRNG
    randomness -- or, in deterministic mode, a seed+counter-derived value
    of the same length and encoding, never real time or randomness."""
    global _deterministic_counter
    if _deterministic_seed is not None:
        _deterministic_counter += 1
        digest = hashlib.sha256(f"{_deterministic_seed}:{_deterministic_counter}".encode()).digest()
        value = int.from_bytes(digest[: _ID_BITS // 8 + 1], "big") & ((1 << _ID_BITS) - 1)
        return encode_crockford(value, _ULID_LENGTH)

    timestamp_ms = time.time_ns() // 1_000_000
    randomness = int.from_bytes(os.urandom(_RANDOM_BITS // 8), "big")
    value = (timestamp_ms << _RANDOM_BITS) | randomness
    return encode_crockford(value, _ULID_LENGTH)


def new_id(prefix: str) -> str:
    """A prefixed ULID, e.g. new_id("mdt") -> 'mdt_01JX8Z6QK4T2N9V0...'."""
    return f"{prefix}_{ulid()}"
