"""HTTP-level fixtures for tests/integration/agents. Container/engine/
session/redis fixtures (postgres_url, session_factory, redis_url,
redis_client) live in the parent tests/integration/conftest.py.

Same TestClient-background-loop precedent as tests/integration/catalog
(ADR 0005 decision 12): Starlette's TestClient runs the ASGI app on its
own background thread with its own event loop, so this fixture builds a
fresh engine/session_factory and a fresh Redis client bound to nothing
until first use inside a request -- construction does no I/O, so it is
safe to build here and touch only from inside `TestClient`'s requests (or
via `.portal.call(...)` for test-side seeding), never from this fixture's
own (pytest-asyncio session-loop) body.

`get_payment_provider` is overridden to a `SimulatorAdapter` -- the app's
own lifespan would otherwise build whatever `PAYMENT_PROVIDER` settings
says (default "razorpay"), and these tests must never make a real
Razorpay call (§28 P7 instruction 4's "LLM_ENABLED=false ... no ... calls
outside the deterministic path" extends the same discipline P5/P6 already
established for payments).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from actl.domain.agent.envelope import AgentEnvelope, MessageType, sign_envelope_ed25519
from actl.infrastructure.cache.nonce import NonceCache
from actl.infrastructure.db.repositories.agent_identities import AgentIdentityRecord
from actl.infrastructure.db.uow import UnitOfWork
from actl.infrastructure.providers.simulator.adapter import SimulatorAdapter
from actl.interfaces.http.deps import get_nonce_cache, get_payment_provider, get_session_factory
from actl.main import app
from actl.platform.clock import SystemClock
from actl.platform.ids import new_id


@dataclass(frozen=True)
class TestIdentity:
    agent_id: str
    key_id: str
    private_key: Ed25519PrivateKey

    @property
    def public_key_hex(self) -> str:
        raw = self.private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        return raw.hex()


def generate_test_identity(agent_id: str) -> TestIdentity:
    private_key = Ed25519PrivateKey.generate()
    raw = private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    key_id = f"ed25519:{hashlib.sha256(raw).hexdigest()[:8]}"
    return TestIdentity(agent_id=agent_id, key_id=key_id, private_key=private_key)


async def seed_identity(
    session_factory: async_sessionmaker[AsyncSession],
    identity: TestIdentity,
    *,
    status: str = "ACTIVE",
    not_before: datetime | None = None,
    expires_at: datetime | None = None,
) -> None:
    now = datetime.now(UTC)
    async with UnitOfWork(session_factory) as uow:
        await uow.agent_identities.add(
            AgentIdentityRecord(
                agent_id=identity.agent_id,
                key_id=identity.key_id,
                alg="Ed25519",
                public_key_hex=identity.public_key_hex,
                status=status,
                not_before=not_before or (now - timedelta(days=1)),
                expires_at=expires_at or (now + timedelta(days=365)),
            )
        )
        await uow.commit()


def build_signed_envelope(
    identity: TestIdentity,
    *,
    to: str,
    type: MessageType,
    body: dict[str, object],
    corr_id: str | None = None,
    msg_id: str | None = None,
    ts: datetime | None = None,
) -> AgentEnvelope:
    draft = AgentEnvelope.model_validate(
        {
            "protocol": "actl.acp/1",
            "msg_id": msg_id or new_id("msg"),
            "ts": ts or datetime.now(UTC),
            "from": identity.agent_id,
            "to": to,
            "corr_id": corr_id or new_id("corr"),
            "type": type,
            "body": body,
        }
    )
    return sign_envelope_ed25519(draft, identity.private_key, identity.key_id)


@dataclass
class AgentTestClient:
    http: TestClient
    session_factory: async_sessionmaker[AsyncSession]
    provider: SimulatorAdapter

    def post_envelope(self, envelope: AgentEnvelope) -> object:
        return self.http.post(
            "/agent/v1/messages", json=envelope.model_dump(mode="json", by_alias=True)
        )

    def seed_identity(
        self,
        identity: TestIdentity,
        *,
        status: str = "ACTIVE",
        not_before: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> None:
        assert self.http.portal is not None, "TestClient not entered as a context manager"
        self.http.portal.call(
            lambda: seed_identity(
                self.session_factory,
                identity,
                status=status,
                not_before=not_before,
                expires_at=expires_at,
            )
        )


@pytest.fixture
def agent_client(postgres_url: str, redis_url: str) -> Iterator[AgentTestClient]:
    test_engine = create_async_engine(postgres_url, pool_size=5, max_overflow=10)
    test_session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    test_redis = Redis.from_url(redis_url)
    simulator = SimulatorAdapter(clock=SystemClock())

    app.dependency_overrides[get_session_factory] = lambda: test_session_factory
    app.dependency_overrides[get_nonce_cache] = lambda: NonceCache(test_redis)
    app.dependency_overrides[get_payment_provider] = lambda: simulator
    try:
        with TestClient(app) as http_client:
            yield AgentTestClient(
                http=http_client, session_factory=test_session_factory, provider=simulator
            )
    finally:
        app.dependency_overrides.pop(get_session_factory, None)
        app.dependency_overrides.pop(get_nonce_cache, None)
        app.dependency_overrides.pop(get_payment_provider, None)
