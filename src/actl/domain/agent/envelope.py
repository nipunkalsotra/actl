"""§8.4 / §14: AgentEnvelope -- every agent-to-agent message, signed.

Pure domain model: canonicalisation, Ed25519 signing/verification, and the
HMAC-SHA256 development-fallback signing/verification §14.1 documents. No
I/O -- identity lookup, replay protection, and persistence all live in
`application/agents/` and `infrastructure/`.
"""

from __future__ import annotations

import hashlib
import hmac as hmac_module
from datetime import datetime
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field

from actl.domain.audit.canonical import JSONValue, jcs

PROTOCOL_ID = "actl.acp/1"
SUPPORTED_MAJOR_VERSIONS = frozenset({"1"})
SUPPORTED_ALGORITHMS = frozenset({"Ed25519", "HMAC-SHA256"})

MessageType = Literal[
    "capability.discover",
    "catalog.query",
    "quote.request",
    "order.propose",
    "order.status",
    "receipt.issue",
    "error",
]

# The six request types a buyer-agent may send; "error" is a response-only shape.
REQUEST_MESSAGE_TYPES: frozenset[str] = frozenset(
    {
        "capability.discover",
        "catalog.query",
        "quote.request",
        "order.propose",
        "order.status",
        "receipt.issue",
    }
)


# Postgres/asyncpg cannot represent a NUL byte in a text value at all
# (`CharacterNotInRepertoireError`) -- any envelope field that later
# reaches a WHERE clause (sig.key_id via agent_identities, msg_id via the
# nonce cache key, ...) must reject one here, at the single earliest
# parse point, rather than crash deep inside a repository with a 500.
_NO_NUL_BYTES = r"^[^\x00]*$"


class SignatureBlock(BaseModel):
    model_config = ConfigDict(frozen=True)

    alg: str = Field(min_length=1, pattern=_NO_NUL_BYTES)
    key_id: str = Field(min_length=1, pattern=_NO_NUL_BYTES)
    value: str = Field(min_length=1, pattern=_NO_NUL_BYTES)


class AgentEnvelope(BaseModel):
    """§8.4. `sig` is None only during construction, before signing
    attaches it -- same draft-then-attach shape as Mandate.signature."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    protocol: str = Field(min_length=1, pattern=_NO_NUL_BYTES)
    msg_id: str = Field(min_length=1, pattern=_NO_NUL_BYTES)
    ts: datetime
    from_: str = Field(alias="from", min_length=1, pattern=_NO_NUL_BYTES)
    to: str = Field(min_length=1, pattern=_NO_NUL_BYTES)
    corr_id: str = Field(min_length=1, pattern=_NO_NUL_BYTES)
    type: MessageType
    body: dict[str, object]
    sig: SignatureBlock | None = None


def _signable_payload(envelope: AgentEnvelope) -> dict[str, JSONValue]:
    """§14.1: "Ed25519 over the canonical JSON of the envelope minus the
    sig field" -- the key is *absent*, not present-and-null."""
    return envelope.model_dump(mode="json", by_alias=True, exclude={"sig"})


def canonical_signing_bytes(envelope: AgentEnvelope) -> bytes:
    return jcs(_signable_payload(envelope)).encode("utf-8")


def is_known_protocol_version(protocol: str) -> bool:
    """§14.1: "protocol: actl.acp/1 is mandatory. An unknown major version
    is rejected outright rather than best-effort parsed."""
    name, sep, version = protocol.partition("/")
    if not sep:
        return False
    major = version.split(".", 1)[0]
    return name == "actl.acp" and major in SUPPORTED_MAJOR_VERSIONS


def is_known_algorithm(alg: str) -> bool:
    return alg in SUPPORTED_ALGORITHMS


def sign_envelope_ed25519(
    envelope: AgentEnvelope, private_key: Ed25519PrivateKey, key_id: str
) -> AgentEnvelope:
    """Returns a new, signed copy -- envelopes are frozen, never mutated."""
    unsigned = envelope.model_copy(update={"sig": None})
    signature = private_key.sign(canonical_signing_bytes(unsigned))
    return unsigned.model_copy(
        update={"sig": SignatureBlock(alg="Ed25519", key_id=key_id, value=signature.hex())}
    )


def verify_envelope_ed25519(envelope: AgentEnvelope, public_key: Ed25519PublicKey) -> bool:
    if envelope.sig is None or envelope.sig.alg != "Ed25519":
        return False
    try:
        signature = bytes.fromhex(envelope.sig.value)
    except ValueError:
        return False
    unsigned = envelope.model_copy(update={"sig": None})
    try:
        public_key.verify(signature, canonical_signing_bytes(unsigned))
    except InvalidSignature:
        return False
    return True


def sign_envelope_hmac(envelope: AgentEnvelope, key: bytes, key_id: str) -> AgentEnvelope:
    """§14.1's documented development fallback."""
    unsigned = envelope.model_copy(update={"sig": None})
    digest = hmac_module.new(key, canonical_signing_bytes(unsigned), hashlib.sha256).hexdigest()
    return unsigned.model_copy(
        update={"sig": SignatureBlock(alg="HMAC-SHA256", key_id=key_id, value=digest)}
    )


def verify_envelope_hmac(envelope: AgentEnvelope, key: bytes) -> bool:
    if envelope.sig is None or envelope.sig.alg != "HMAC-SHA256":
        return False
    unsigned = envelope.model_copy(update={"sig": None})
    expected = hmac_module.new(key, canonical_signing_bytes(unsigned), hashlib.sha256).hexdigest()
    return hmac_module.compare_digest(expected, envelope.sig.value)
