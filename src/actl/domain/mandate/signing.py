"""HMAC-SHA256 signing/verification over spec_hash (§8.1 NOTE): a
server-held integrity seal attesting the platform locked exactly the spec
the human confirmed. The key is caller-supplied — domain never reads a
secret from the environment."""

from __future__ import annotations

import hashlib
import hmac


def sign_spec_hash(spec_hash: str, key: bytes) -> str:
    return hmac.new(key, spec_hash.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_signature(spec_hash: str, key: bytes, signature_value: str) -> bool:
    expected = sign_spec_hash(spec_hash, key)
    return hmac.compare_digest(expected, signature_value)
