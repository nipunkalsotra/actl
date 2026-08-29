"""§28 P7 instruction 3, end-to-end through the real HTTP pipeline (the
nonce cache in isolation is covered by test_nonce_cache.py): sequential
duplicate rejection, too-old/too-future timestamps, and Redis-unavailable
failing closed at the full `POST /agent/v1/messages` level."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis

from actl.infrastructure.cache.nonce import NonceCache
from actl.interfaces.http.deps import get_nonce_cache
from actl.main import app
from tests.integration.agents.conftest import (
    AgentTestClient,
    build_signed_envelope,
    generate_test_identity,
)

_BODY = {"supported_protocols": ["actl.acp/1"]}


def test_first_delivery_is_accepted(agent_client: AgentTestClient) -> None:
    identity = generate_test_identity("agt_first")
    agent_client.seed_identity(identity)

    envelope = build_signed_envelope(
        identity, to="agt_merchant_01", type="capability.discover", body=_BODY
    )
    response = agent_client.post_envelope(envelope)

    assert response.status_code == 200  # type: ignore[attr-defined]


def test_replayed_message_is_rejected(agent_client: AgentTestClient) -> None:
    identity = generate_test_identity("agt_replay")
    agent_client.seed_identity(identity)

    envelope = build_signed_envelope(
        identity, to="agt_merchant_01", type="capability.discover", body=_BODY
    )
    first = agent_client.post_envelope(envelope)
    second = agent_client.post_envelope(envelope)

    assert first.status_code == 200  # type: ignore[attr-defined]
    assert second.status_code == 409  # type: ignore[attr-defined]
    assert second.json()["reason_code"] == "REPLAYED_MESSAGE"  # type: ignore[attr-defined]


def test_clock_skew_beyond_120s_in_the_past_is_rejected(agent_client: AgentTestClient) -> None:
    identity = generate_test_identity("agt_too_old")
    agent_client.seed_identity(identity)

    envelope = build_signed_envelope(
        identity,
        to="agt_merchant_01",
        type="capability.discover",
        body=_BODY,
        ts=datetime.now(UTC) - timedelta(seconds=200),
    )
    response = agent_client.post_envelope(envelope)

    assert response.status_code == 400  # type: ignore[attr-defined]
    assert response.json()["reason_code"] == "CLOCK_SKEW"  # type: ignore[attr-defined]


def test_clock_skew_beyond_120s_in_the_future_is_rejected(agent_client: AgentTestClient) -> None:
    identity = generate_test_identity("agt_too_future")
    agent_client.seed_identity(identity)

    envelope = build_signed_envelope(
        identity,
        to="agt_merchant_01",
        type="capability.discover",
        body=_BODY,
        ts=datetime.now(UTC) + timedelta(seconds=200),
    )
    response = agent_client.post_envelope(envelope)

    assert response.status_code == 400  # type: ignore[attr-defined]
    assert response.json()["reason_code"] == "CLOCK_SKEW"  # type: ignore[attr-defined]


def test_timestamp_within_the_120s_window_is_accepted(agent_client: AgentTestClient) -> None:
    identity = generate_test_identity("agt_within_window")
    agent_client.seed_identity(identity)

    envelope = build_signed_envelope(
        identity,
        to="agt_merchant_01",
        type="capability.discover",
        body=_BODY,
        ts=datetime.now(UTC) - timedelta(seconds=100),
    )
    response = agent_client.post_envelope(envelope)

    assert response.status_code == 200  # type: ignore[attr-defined]


def test_redis_unavailable_fails_closed_end_to_end(agent_client: AgentTestClient) -> None:
    """§14: replay protection is never silently disabled -- an unreachable
    Redis must reject the message (503, retryable), never let it through
    as if it were a first delivery."""
    identity = generate_test_identity("agt_redis_down")
    agent_client.seed_identity(identity)

    unreachable = Redis.from_url(
        "redis://127.0.0.1:1/0", socket_connect_timeout=1, socket_timeout=1
    )
    app.dependency_overrides[get_nonce_cache] = lambda: NonceCache(unreachable)
    try:
        envelope = build_signed_envelope(
            identity, to="agt_merchant_01", type="capability.discover", body=_BODY
        )
        response = agent_client.post_envelope(envelope)
    finally:
        del app.dependency_overrides[get_nonce_cache]

    assert response.status_code == 503  # type: ignore[attr-defined]
    body = response.json()  # type: ignore[attr-defined]
    assert body["reason_code"] == "REPLAY_CHECK_UNAVAILABLE"
    assert body["retryable"] is True
