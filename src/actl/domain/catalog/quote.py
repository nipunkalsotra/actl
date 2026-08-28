"""Quote v1 (§8.4, §13.2): a price pinned to a catalog version and a
deadline. Hashing and signing reuse P1's exact primitives —
`actl.domain.audit.canonical.jcs` and `actl.domain.mandate.signing`'s
HMAC-SHA256 functions — rather than inventing a second canonicaliser or
signing scheme (§28 P4 instruction 3). HMAC-SHA256 is §14.1's documented
"development fallback"; this is a 100% test-mode build (config.py), so that
fallback is what's actually wired up. See
docs/adr/0005-p4-catalog-quote-decisions.md.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from actl.domain.audit.canonical import JSONValue, jcs
from actl.domain.mandate.signing import sign_spec_hash, verify_signature

QUOTE_TOKEN_PREFIX = "qt_v1"

_HASH_EXCLUDE = {"quote_token", "quote_hash"}


class Quote(BaseModel):
    """§8.4. `quote_token`/`quote_hash` are None until `issue_quote` (in
    application/catalog_service.py) computes and attaches them — same
    draft-then-attach shape as P1's Mandate (spec_hash/signature)."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    schema_: Literal["actl.quote/v1"] = Field(alias="schema", default="actl.quote/v1")
    quote_id: str
    sku: str
    mandate_id: str
    unit_price_minor: StrictInt
    nights: int
    total_minor: StrictInt
    currency: Literal["INR"] = "INR"
    catalog_version: int
    refundable: bool
    expires_at: datetime
    quote_token: str | None = None
    quote_hash: str | None = None


def _hashable_payload(quote: Quote) -> dict[str, JSONValue]:
    return quote.model_dump(mode="json", by_alias=True, exclude=_HASH_EXCLUDE)


def compute_quote_hash(quote: Quote) -> str:
    """sha256 over RFC 8785 canonical JSON of every field except
    quote_hash/quote_token themselves — same shape as
    mandate.hashing.compute_spec_hash."""
    digest = hashlib.sha256(jcs(_hashable_payload(quote)).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def build_quote_token(quote: Quote, quote_hash: str, key: bytes) -> str:
    """`qt_v1.<base64url(jcs(payload))>.<HMAC-SHA256(quote_hash)>` — a
    compact, self-contained bearer token: the payload segment lets a holder
    read the quote without a DB round trip, the signature segment (over
    quote_hash, via the existing sign_spec_hash primitive) lets them prove
    it came from this server unmodified."""
    canonical_bytes = jcs(_hashable_payload(quote)).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(canonical_bytes).rstrip(b"=").decode("ascii")
    signature = sign_spec_hash(quote_hash, key)
    return f"{QUOTE_TOKEN_PREFIX}.{payload_b64}.{signature}"


def parse_and_verify_quote_token(token: str, key: bytes) -> dict[str, JSONValue]:
    """Decode and verify a quote_token's signature, returning its embedded
    payload dict. Raises ValueError on any malformed or tampered token —
    never returns a partially-trusted result."""
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != QUOTE_TOKEN_PREFIX:
        raise ValueError("malformed quote_token")
    _, payload_b64, signature = parts
    padding = "=" * (-len(payload_b64) % 4)
    try:
        canonical_bytes = base64.urlsafe_b64decode(payload_b64 + padding)
    except Exception as exc:
        raise ValueError("malformed quote_token payload") from exc

    quote_hash = f"sha256:{hashlib.sha256(canonical_bytes).hexdigest()}"
    if not verify_signature(quote_hash, key, signature):
        raise ValueError("quote_token signature invalid")

    payload: dict[str, JSONValue] = json.loads(canonical_bytes)
    return payload
