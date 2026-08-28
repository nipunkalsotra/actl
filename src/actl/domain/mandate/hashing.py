"""spec_hash (§8.1): sha256 over RFC 8785 canonical JSON of every mandate
field except spec_hash and signature itself."""

from __future__ import annotations

import hashlib

from actl.domain.audit.canonical import jcs
from actl.domain.mandate.models import Mandate


def compute_spec_hash(mandate: Mandate) -> str:
    payload = mandate.model_dump(mode="json", by_alias=True, exclude={"spec_hash", "signature"})
    digest = hashlib.sha256(jcs(payload).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def verify_spec_hash(mandate: Mandate) -> bool:
    """I-M2: spec_hash recomputed at any later time MUST equal the stored
    value, or the mandate is treated as compromised."""
    return mandate.spec_hash == compute_spec_hash(mandate)
