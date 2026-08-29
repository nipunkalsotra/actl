"""§14.1 agent-envelope verification pipeline: protocol/algorithm checks,
identity resolution, signature verification, replay protection, timestamp
skew -- in that order, before any message-specific business handling
(§28 P7 instruction 4). Every one of the seven message handlers in
`application.agents.merchant` routes through `verify_envelope` first.
"""

from __future__ import annotations

from dataclasses import dataclass

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from actl.config import settings
from actl.domain.agent.envelope import (
    AgentEnvelope,
    is_known_algorithm,
    is_known_protocol_version,
    verify_envelope_ed25519,
    verify_envelope_hmac,
)
from actl.domain.policy.reason_codes import ReasonCode
from actl.infrastructure.cache.nonce import NonceCache, NonceCacheUnavailable
from actl.infrastructure.db.uow import UnitOfWork
from actl.platform.clock import Clock

SKEW_WINDOW_S = 120


@dataclass(frozen=True)
class EnvelopeVerified:
    envelope: AgentEnvelope
    identity_agent_id: str


@dataclass(frozen=True)
class EnvelopeRejected:
    reason_code: ReasonCode
    message: str
    retryable: bool = False


EnvelopeVerificationResult = EnvelopeVerified | EnvelopeRejected


async def verify_envelope(
    uow: UnitOfWork,
    nonce_cache: NonceCache,
    clock: Clock,
    envelope: AgentEnvelope,
) -> EnvelopeVerificationResult:
    """Never raises for an untrusted envelope -- every rejection is a
    typed `EnvelopeRejected`, matching §28 P7 instruction 1's "reject ...
    safely"."""
    if not is_known_protocol_version(envelope.protocol):
        return EnvelopeRejected(
            ReasonCode.UNKNOWN_PROTOCOL_VERSION, f"unsupported protocol {envelope.protocol!r}"
        )

    if envelope.sig is None or not is_known_algorithm(envelope.sig.alg):
        alg = envelope.sig.alg if envelope.sig is not None else "<missing>"
        return EnvelopeRejected(
            ReasonCode.UNKNOWN_ALGORITHM, f"unsupported signature algorithm {alg!r}"
        )

    # §14.1's HMAC-SHA256 "development fallback" is disabled in every
    # normal runtime -- Ed25519 is the only algorithm this pipeline
    # accepts unless agent_envelope_hmac_test_only is explicitly on, and
    # that setting can only ever be true under pytest (config.py's
    # _enforce_no_hmac_outside_pytest refuses to start the process
    # otherwise). Checked before identity resolution, so an HMAC-mode
    # identity accidentally present in the registry still can't be used
    # to authenticate a message outside test-only configuration.
    if envelope.sig.alg == "HMAC-SHA256" and not settings.agent_envelope_hmac_test_only:
        return EnvelopeRejected(
            ReasonCode.UNKNOWN_ALGORITHM,
            "HMAC-SHA256 envelope signing is disabled outside test-only configuration",
        )

    # Identity validation -- resolve by key_id, then confirm it actually
    # belongs to the claimed `from` agent (a key_id valid for one agent
    # must never authenticate a message claiming to be from another).
    identity = await uow.agent_identities.get_by_key_id(envelope.sig.key_id)
    if identity is None or identity.agent_id != envelope.from_:
        return EnvelopeRejected(
            ReasonCode.IDENTITY_UNKNOWN, "unknown key_id or agent/key_id mismatch"
        )
    if identity.status == "REVOKED":
        return EnvelopeRejected(
            ReasonCode.IDENTITY_REVOKED, f"identity {identity.agent_id} is revoked"
        )
    now = clock.now()
    if now < identity.not_before or now >= identity.expires_at:
        return EnvelopeRejected(
            ReasonCode.IDENTITY_EXPIRED, f"identity {identity.agent_id} is expired"
        )

    # Envelope verification -- the signature itself, using the resolved identity's key.
    verified = False
    if identity.alg == "Ed25519" and envelope.sig.alg == "Ed25519" and identity.public_key_hex:
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(identity.public_key_hex))
        verified = verify_envelope_ed25519(envelope, public_key)
    elif (
        identity.alg == "HMAC-SHA256" and envelope.sig.alg == "HMAC-SHA256" and identity.hmac_secret
    ):
        verified = verify_envelope_hmac(envelope, identity.hmac_secret.encode("utf-8"))
    if not verified:
        return EnvelopeRejected(
            ReasonCode.SIGNATURE_INVALID, "envelope signature verification failed"
        )

    # Replay protection -- fails closed: an unreachable nonce cache is a
    # rejection, never treated as "first delivery" (§14, §28 P7 instruction 3).
    try:
        won = await nonce_cache.claim(envelope.msg_id)
    except NonceCacheUnavailable as exc:
        return EnvelopeRejected(
            ReasonCode.REPLAY_CHECK_UNAVAILABLE,
            f"replay protection unavailable: {exc}",
            retryable=True,
        )
    if not won:
        return EnvelopeRejected(
            ReasonCode.REPLAYED_MESSAGE, f"msg_id {envelope.msg_id} already seen"
        )

    # Timestamp skew -- checked last, via the injected Clock only.
    skew_s = abs((now - envelope.ts).total_seconds())
    if skew_s > SKEW_WINDOW_S:
        return EnvelopeRejected(
            ReasonCode.CLOCK_SKEW, f"timestamp skew {skew_s:.1f}s exceeds {SKEW_WINDOW_S}s"
        )

    return EnvelopeVerified(envelope=envelope, identity_agent_id=identity.agent_id)
