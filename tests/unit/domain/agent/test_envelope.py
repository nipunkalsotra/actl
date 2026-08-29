"""§8.4 / §14.1: AgentEnvelope canonicalisation and signing. Pure, no I/O."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from actl.domain.agent.envelope import (
    AgentEnvelope,
    canonical_signing_bytes,
    is_known_algorithm,
    is_known_protocol_version,
    sign_envelope_ed25519,
    sign_envelope_hmac,
    verify_envelope_ed25519,
    verify_envelope_hmac,
)

_TS = datetime(2026, 8, 28, 9, 4, 9, 881000, tzinfo=UTC)


def _draft(**overrides: object) -> AgentEnvelope:
    fields: dict[str, object] = {
        "protocol": "actl.acp/1",
        "msg_id": "msg_01JX8Z71F",
        "ts": _TS,
        "from": "agt_buyer_01",
        "to": "agt_merchant_01",
        "corr_id": "01JX8Z7C1M4RQ",
        "type": "capability.discover",
        "body": {"supported_protocols": ["actl.acp/1"]},
    }
    fields.update(overrides)
    return AgentEnvelope.model_validate(fields)


def test_ed25519_sign_then_verify_round_trips() -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    signed = sign_envelope_ed25519(_draft(), private_key, "ed25519:test01")

    assert signed.sig is not None
    assert signed.sig.alg == "Ed25519"
    assert verify_envelope_ed25519(signed, public_key) is True


def test_ed25519_verification_fails_with_the_wrong_public_key() -> None:
    private_key = Ed25519PrivateKey.generate()
    other_public_key = Ed25519PrivateKey.generate().public_key()
    signed = sign_envelope_ed25519(_draft(), private_key, "ed25519:test01")

    assert verify_envelope_ed25519(signed, other_public_key) is False


def test_ed25519_verification_fails_if_body_tampered_after_signing() -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    signed = sign_envelope_ed25519(_draft(), private_key, "ed25519:test01")

    tampered = signed.model_copy(update={"body": {"supported_protocols": ["actl.acp/2"]}})

    assert verify_envelope_ed25519(tampered, public_key) is False


def test_ed25519_verification_fails_on_malformed_signature_hex() -> None:
    private_key = Ed25519PrivateKey.generate()
    signed = sign_envelope_ed25519(_draft(), private_key, "ed25519:test01")
    corrupted = signed.model_copy(update={"sig": signed.sig.model_copy(update={"value": "zz"})})

    assert verify_envelope_ed25519(corrupted, private_key.public_key()) is False


def test_signing_never_mutates_the_original_envelope() -> None:
    draft = _draft()
    private_key = Ed25519PrivateKey.generate()
    sign_envelope_ed25519(draft, private_key, "ed25519:test01")

    assert draft.sig is None  # frozen, unmutated


def test_canonical_signing_bytes_omit_the_sig_key_entirely() -> None:
    private_key = Ed25519PrivateKey.generate()
    signed = sign_envelope_ed25519(_draft(), private_key, "ed25519:test01")

    payload = json.loads(canonical_signing_bytes(signed))
    assert "sig" not in payload


def test_hmac_sign_then_verify_round_trips() -> None:
    key = b"shared-dev-fallback-secret"
    signed = sign_envelope_hmac(_draft(), key, "hmac:test01")

    assert signed.sig is not None
    assert signed.sig.alg == "HMAC-SHA256"
    assert verify_envelope_hmac(signed, key) is True


def test_hmac_verification_fails_with_the_wrong_key() -> None:
    signed = sign_envelope_hmac(_draft(), b"correct-key", "hmac:test01")
    assert verify_envelope_hmac(signed, b"wrong-key") is False


@pytest.mark.parametrize(
    ("protocol", "expected"),
    [
        ("actl.acp/1", True),
        ("actl.acp/1.2", True),
        ("actl.acp/2", False),
        ("actl.acp/0", False),
        ("other.proto/1", False),
        ("actl.acp", False),
        ("", False),
    ],
)
def test_protocol_version_recognition(protocol: str, expected: bool) -> None:
    assert is_known_protocol_version(protocol) is expected


@pytest.mark.parametrize(
    ("alg", "expected"),
    [("Ed25519", True), ("HMAC-SHA256", True), ("RSA", False), ("", False), ("ed25519", False)],
)
def test_algorithm_recognition(alg: str, expected: bool) -> None:
    assert is_known_algorithm(alg) is expected


def test_envelope_rejects_missing_required_fields() -> None:
    with pytest.raises(Exception):  # noqa: B017 -- pydantic.ValidationError
        AgentEnvelope.model_validate({"protocol": "actl.acp/1"})


def test_envelope_rejects_empty_string_fields() -> None:
    with pytest.raises(Exception):  # noqa: B017 -- pydantic.ValidationError
        _draft(msg_id="")


def test_envelope_rejects_unknown_message_type() -> None:
    with pytest.raises(Exception):  # noqa: B017 -- pydantic.ValidationError
        _draft(type="not.a.real.type")
