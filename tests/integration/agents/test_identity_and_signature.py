"""§28 P7 instruction 2: agent identity active/revoked/expired behaviour,
and instruction 1's "reject ... unknown algorithms, unknown versions,
missing fields, invalid signatures, and invalid key IDs safely"."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.integration.agents.conftest import (
    AgentTestClient,
    build_signed_envelope,
    generate_test_identity,
)

_BODY = {"supported_protocols": ["actl.acp/1"]}


def test_active_identity_is_accepted(agent_client: AgentTestClient) -> None:
    identity = generate_test_identity("agt_active")
    agent_client.seed_identity(identity)

    envelope = build_signed_envelope(
        identity, to="agt_merchant_01", type="capability.discover", body=_BODY
    )
    response = agent_client.post_envelope(envelope)

    assert response.status_code == 200  # type: ignore[attr-defined]


def test_revoked_identity_is_rejected(agent_client: AgentTestClient) -> None:
    identity = generate_test_identity("agt_revoked")
    agent_client.seed_identity(identity, status="REVOKED")

    envelope = build_signed_envelope(
        identity, to="agt_merchant_01", type="capability.discover", body=_BODY
    )
    response = agent_client.post_envelope(envelope)

    assert response.status_code == 403  # type: ignore[attr-defined]
    assert response.json()["reason_code"] == "IDENTITY_REVOKED"  # type: ignore[attr-defined]


def test_expired_identity_is_rejected(agent_client: AgentTestClient) -> None:
    identity = generate_test_identity("agt_expired")
    now = datetime.now(UTC)
    agent_client.seed_identity(
        identity, not_before=now - timedelta(days=10), expires_at=now - timedelta(days=1)
    )

    envelope = build_signed_envelope(
        identity, to="agt_merchant_01", type="capability.discover", body=_BODY
    )
    response = agent_client.post_envelope(envelope)

    assert response.status_code == 403  # type: ignore[attr-defined]
    assert response.json()["reason_code"] == "IDENTITY_EXPIRED"  # type: ignore[attr-defined]


def test_not_yet_valid_identity_is_rejected(agent_client: AgentTestClient) -> None:
    identity = generate_test_identity("agt_future")
    now = datetime.now(UTC)
    agent_client.seed_identity(
        identity, not_before=now + timedelta(days=1), expires_at=now + timedelta(days=365)
    )

    envelope = build_signed_envelope(
        identity, to="agt_merchant_01", type="capability.discover", body=_BODY
    )
    response = agent_client.post_envelope(envelope)

    assert response.status_code == 403  # type: ignore[attr-defined]
    assert response.json()["reason_code"] == "IDENTITY_EXPIRED"  # type: ignore[attr-defined]


def test_unknown_key_id_is_rejected(agent_client: AgentTestClient) -> None:
    identity = generate_test_identity("agt_unregistered")  # never seeded

    envelope = build_signed_envelope(
        identity, to="agt_merchant_01", type="capability.discover", body=_BODY
    )
    response = agent_client.post_envelope(envelope)

    assert response.status_code == 401  # type: ignore[attr-defined]
    assert response.json()["reason_code"] == "IDENTITY_UNKNOWN"  # type: ignore[attr-defined]


def test_key_id_valid_for_a_different_agent_is_rejected(agent_client: AgentTestClient) -> None:
    """A registered key_id must only authenticate the exact agent it was
    issued to -- signing as one agent then claiming `from` another must
    never be accepted even with a technically-valid signature."""
    real = generate_test_identity("agt_real_owner")
    agent_client.seed_identity(real)

    envelope = build_signed_envelope(
        real, to="agt_merchant_01", type="capability.discover", body=_BODY
    )
    impersonating = envelope.model_copy(update={"from_": "agt_someone_else"})
    # re-sign isn't attempted -- the point is that even the *original*,
    # validly-signed envelope must bind key_id to exactly one agent_id.
    response = agent_client.post_envelope(impersonating)

    assert response.status_code == 401  # type: ignore[attr-defined]


def test_invalid_signature_is_rejected(agent_client: AgentTestClient) -> None:
    identity = generate_test_identity("agt_tampered")
    agent_client.seed_identity(identity)

    envelope = build_signed_envelope(
        identity, to="agt_merchant_01", type="capability.discover", body=_BODY
    )
    tampered = envelope.model_copy(update={"body": {"supported_protocols": ["actl.acp/999"]}})
    response = agent_client.post_envelope(tampered)

    assert response.status_code == 401  # type: ignore[attr-defined]
    assert response.json()["reason_code"] == "SIGNATURE_INVALID"  # type: ignore[attr-defined]


def test_unknown_protocol_version_is_rejected(agent_client: AgentTestClient) -> None:
    identity = generate_test_identity("agt_bad_version")
    agent_client.seed_identity(identity)

    envelope = build_signed_envelope(
        identity, to="agt_merchant_01", type="capability.discover", body=_BODY
    )
    bad = envelope.model_copy(update={"protocol": "actl.acp/99"})
    response = agent_client.post_envelope(bad)

    assert response.status_code == 400  # type: ignore[attr-defined]
    assert response.json()["reason_code"] == "UNKNOWN_PROTOCOL_VERSION"  # type: ignore[attr-defined]


def test_unknown_algorithm_is_rejected(agent_client: AgentTestClient) -> None:
    identity = generate_test_identity("agt_bad_alg")
    agent_client.seed_identity(identity)

    envelope = build_signed_envelope(
        identity, to="agt_merchant_01", type="capability.discover", body=_BODY
    )
    assert envelope.sig is not None
    bad = envelope.model_copy(update={"sig": envelope.sig.model_copy(update={"alg": "RSA"})})
    response = agent_client.post_envelope(bad)

    assert response.status_code == 400  # type: ignore[attr-defined]
    assert response.json()["reason_code"] == "UNKNOWN_ALGORITHM"  # type: ignore[attr-defined]


def test_missing_fields_are_rejected(agent_client: AgentTestClient) -> None:
    response = agent_client.http.post("/agent/v1/messages", json={"protocol": "actl.acp/1"})

    assert response.status_code == 400  # type: ignore[attr-defined]
    assert response.json()["reason_code"] == "MALFORMED_REQUEST"  # type: ignore[attr-defined]


def test_malformed_encoding_is_rejected(agent_client: AgentTestClient) -> None:
    response = agent_client.http.post(
        "/agent/v1/messages",
        content=b"not valid json{{{",
        headers={"content-type": "application/json"},
    )

    assert response.status_code in (400, 422)  # type: ignore[attr-defined]


def test_no_sensitive_material_is_echoed_in_a_rejection(agent_client: AgentTestClient) -> None:
    """§28 P7 instruction 1: never log/leak private keys, signatures, or
    sensitive envelope contents. The rejection response itself is one
    concrete surface this must hold on."""
    identity = generate_test_identity("agt_secret_check")
    agent_client.seed_identity(identity)

    envelope = build_signed_envelope(
        identity, to="agt_merchant_01", type="capability.discover", body=_BODY
    )
    assert envelope.sig is not None
    tampered = envelope.model_copy(update={"body": {"supported_protocols": ["tampered"]}})
    response = agent_client.post_envelope(tampered)

    body_text = response.text  # type: ignore[attr-defined]
    assert envelope.sig.value not in body_text
