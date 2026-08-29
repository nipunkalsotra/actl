"""Follow-up correction: Ed25519 must be the only signing algorithm
`POST /agent/v1/messages` accepts in every normal runtime -- §14.1
documents HMAC-SHA256 as a development fallback, but `agent_client`
never sets `AGENT_ENVELOPE_HMAC_TEST_ONLY`, so `settings.
agent_envelope_hmac_test_only` is False here exactly as it is in every
real deployment. This proves the gate in `envelope_service.verify_
envelope` actually closes the fallback, not just that the flag defaults
off -- an HMAC-signed envelope, from an identity genuinely registered as
HMAC-SHA256, must still be rejected.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from actl.config import settings
from actl.domain.agent.envelope import AgentEnvelope, sign_envelope_hmac
from actl.infrastructure.db.repositories.agent_identities import AgentIdentityRecord
from actl.infrastructure.db.uow import UnitOfWork
from actl.platform.ids import new_id
from tests.integration.agents.conftest import AgentTestClient

_MERCHANT = "agt_merchant_01"


def test_agent_envelope_hmac_test_only_flag_is_off_by_default() -> None:
    assert settings.agent_envelope_hmac_test_only is False


def test_hmac_signed_envelope_is_rejected_in_normal_configuration(
    agent_client: AgentTestClient,
) -> None:
    agent_id = new_id("agt_hmac")
    key_id = "hmac:test-only-01"
    secret = b"hmac-test-only-shared-secret"

    async def _seed() -> None:
        now = datetime.now(UTC)
        async with UnitOfWork(agent_client.session_factory) as uow:
            await uow.agent_identities.add(
                AgentIdentityRecord(
                    agent_id=agent_id,
                    key_id=key_id,
                    alg="HMAC-SHA256",
                    hmac_secret=secret.decode("utf-8"),
                    status="ACTIVE",
                    not_before=now - timedelta(days=1),
                    expires_at=now + timedelta(days=365),
                )
            )
            await uow.commit()

    assert agent_client.http.portal is not None
    agent_client.http.portal.call(_seed)

    draft = AgentEnvelope.model_validate(
        {
            "protocol": "actl.acp/1",
            "msg_id": new_id("msg"),
            "ts": datetime.now(UTC),
            "from": agent_id,
            "to": _MERCHANT,
            "corr_id": new_id("corr"),
            "type": "capability.discover",
            "body": {"supported_protocols": ["actl.acp/1"]},
        }
    )
    envelope = sign_envelope_hmac(draft, secret, key_id)
    assert envelope.sig is not None
    assert envelope.sig.alg == "HMAC-SHA256"

    resp = agent_client.post_envelope(envelope)
    assert resp.status_code == 400, resp.text  # type: ignore[attr-defined]
    body = resp.json()  # type: ignore[attr-defined]
    assert body["reason_code"] == "UNKNOWN_ALGORITHM"
    assert "disabled outside test-only configuration" in body["message"]
    # No sensitive material (the shared secret) leaks into the rejection.
    assert secret.decode("utf-8") not in body["message"]
