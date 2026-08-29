"""Wiring smoke test: one signed capability.discover round trip through
the real HTTP app, real Postgres, real Redis."""

from __future__ import annotations

from tests.integration.agents.conftest import (
    AgentTestClient,
    build_signed_envelope,
    generate_test_identity,
)


def test_capability_discover_round_trip(agent_client: AgentTestClient) -> None:
    identity = generate_test_identity("agt_buyer_smoke")
    agent_client.seed_identity(identity)

    envelope = build_signed_envelope(
        identity,
        to="agt_merchant_01",
        type="capability.discover",
        body={"supported_protocols": ["actl.acp/1"]},
    )

    response = agent_client.post_envelope(envelope)

    assert response.status_code == 200, response.text  # type: ignore[attr-defined]
    data = response.json()  # type: ignore[attr-defined]
    assert data["type"] == "capability.discover"
    assert data["body"]["protocol"] == "actl.acp/1"
    assert data["sig"]["alg"] == "Ed25519"
    assert data["from"] == "agt_merchant_01"
    assert data["to"] == "agt_buyer_smoke"
