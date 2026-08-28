"""RFC 8785 JSON Canonicalization Scheme (JCS). Pure stdlib, zero I/O.

Two properties this buys: (1) two independent implementations, given the
same logical JSON value, produce byte-identical output — that's what makes
spec_hash and inputs_digest reproducible and independently verifiable; (2)
object key order stops being meaningful, so a re-ordered-but-equal payload
still hashes the same.

Verified against the official test vectors from the RFC's own reference
implementation (github.com/cyberphone/json-canonicalization/tree/master/testdata),
see tests/unit/domain/audit/test_canonical.py.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from decimal import Decimal

JSONValue = None | bool | int | float | str | Sequence["JSONValue"] | Mapping[str, "JSONValue"]

_ESCAPE_MAP: dict[str, str] = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def jcs(value: JSONValue) -> str:
    """Canonical JSON string for `value` per RFC 8785."""
    return _encode(value)


def _encode(value: JSONValue) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return _encode_string(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _encode_number(value)
    if isinstance(value, Mapping):
        items = sorted(value.items(), key=lambda kv: kv[0].encode("utf-16-be"))
        body = ",".join(f"{_encode_string(k)}:{_encode(v)}" for k, v in items)
        return "{" + body + "}"
    if isinstance(value, Sequence):
        return "[" + ",".join(_encode(v) for v in value) + "]"
    raise TypeError(f"not JSON-serialisable: {type(value)!r}")


def _encode_string(s: str) -> str:
    out = ['"']
    for ch in s:
        if ch in _ESCAPE_MAP:
            out.append(_ESCAPE_MAP[ch])
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _encode_number(x: float) -> str:
    """ECMAScript Number::toString (RFC 8785 §3.2.2.3): shortest round-trip
    digits (which Python's repr() already computes) laid out per the spec's
    fixed/exponential threshold rules, which differ from Python's own."""
    if math.isnan(x) or math.isinf(x):
        raise ValueError(f"{x!r} is not representable in JSON")
    if x == 0.0:
        return "0"
    sign = "-" if x < 0 else ""
    _, digits, exponent = Decimal(repr(abs(x))).normalize().as_tuple()
    assert isinstance(exponent, int)
    digit_str = "".join(map(str, digits))
    k = len(digit_str)
    n = exponent + k
    if k <= n <= 21:
        body = digit_str + "0" * (n - k)
    elif 0 < n <= 21:
        body = digit_str[:n] + "." + digit_str[n:]
    elif -6 < n <= 0:
        body = "0." + "0" * (-n) + digit_str
    else:
        mantissa = digit_str[0] if k == 1 else digit_str[0] + "." + digit_str[1:]
        e = n - 1
        body = f"{mantissa}e{'+' if e >= 0 else '-'}{abs(e)}"
    return sign + body
