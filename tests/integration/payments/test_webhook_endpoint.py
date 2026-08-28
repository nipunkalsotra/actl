"""§28 P5 webhook security correction: the constant-time signature check
in `process_webhook_delivery` runs before any database call, so a
missing, malformed, or invalid `X-Razorpay-Signature` is rejected and
leaves nothing persisted. Real HTTP layer (TestClient), real Postgres --
proves both the returned status code and the resulting database state,
per docs/adr/0006-p5-payments-decisions.md decision 13 (corrected).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from actl.application.ports import PaymentProvider
from actl.infrastructure.db.uow import UnitOfWork
from actl.infrastructure.providers.simulator.adapter import SimulatorAdapter
from actl.interfaces.http.deps import get_payment_provider, get_uow
from actl.main import app
from actl.platform.clock import SystemClock

WEBHOOK_PATH = "/webhooks/razorpay"


@dataclass
class WebhookTestClient:
    http: TestClient
    session_factory: async_sessionmaker[AsyncSession]
    provider: SimulatorAdapter

    def webhook_event_count(self, event_id: str) -> int:
        assert self.http.portal is not None, "TestClient not entered as a context manager"
        return self.http.portal.call(_count_webhook_events, self.session_factory, event_id)


async def _count_webhook_events(
    session_factory: async_sessionmaker[AsyncSession], event_id: str
) -> int:
    async with UnitOfWork(session_factory) as uow:
        record = await uow.webhook_events.get_by_provider_event_id(event_id)
    return 0 if record is None else 1


@pytest.fixture
def client(postgres_url: str) -> Iterator[WebhookTestClient]:
    test_engine = create_async_engine(postgres_url, pool_size=5, max_overflow=10)
    test_session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    provider = SimulatorAdapter(clock=SystemClock())

    async def _override_get_uow() -> AsyncIterator[UnitOfWork]:
        async with UnitOfWork(test_session_factory) as uow:
            yield uow

    def _override_get_provider() -> PaymentProvider:
        return provider

    app.dependency_overrides[get_uow] = _override_get_uow
    app.dependency_overrides[get_payment_provider] = _override_get_provider
    try:
        with TestClient(app) as http_client:
            yield WebhookTestClient(
                http=http_client, session_factory=test_session_factory, provider=provider
            )
    finally:
        app.dependency_overrides.pop(get_uow, None)
        app.dependency_overrides.pop(get_payment_provider, None)


def _valid_delivery(wtc: WebhookTestClient) -> tuple[bytes, str, str]:
    """A real, validly-signed payment.captured delivery -- (raw_body,
    signature, event_id)."""
    return wtc.provider.build_webhook_payload(
        "payment.captured",
        provider_order_id="order_test",
        provider_payment_id="pay_test",
        amount_minor=100000,
    )


# ---- case 1: missing signature ------------------------------------------


def test_missing_signature_is_rejected_and_persists_nothing(client: WebhookTestClient) -> None:
    raw_body, _valid_sig, event_id = _valid_delivery(client)

    resp = client.http.post(
        WEBHOOK_PATH,
        content=raw_body,
        headers={"X-Razorpay-Event-Id": event_id},  # no X-Razorpay-Signature at all
    )

    assert resp.status_code in (400, 401)
    assert client.webhook_event_count(event_id) == 0


# ---- case 2: malformed signature -----------------------------------------


def test_malformed_signature_is_rejected_and_persists_nothing(client: WebhookTestClient) -> None:
    raw_body, _valid_sig, event_id = _valid_delivery(client)

    resp = client.http.post(
        WEBHOOK_PATH,
        content=raw_body,
        headers={
            "X-Razorpay-Signature": "not-a-valid-hex-signature!!",
            "X-Razorpay-Event-Id": event_id,
        },
    )

    assert resp.status_code in (400, 401)
    assert client.webhook_event_count(event_id) == 0


# ---- case 3: invalid (well-formed but wrong) signature --------------------


def test_invalid_signature_is_rejected_and_persists_nothing(client: WebhookTestClient) -> None:
    raw_body, valid_sig, event_id = _valid_delivery(client)
    tampered = valid_sig[:-1] + ("0" if valid_sig[-1] != "0" else "1")

    resp = client.http.post(
        WEBHOOK_PATH,
        content=raw_body,
        headers={"X-Razorpay-Signature": tampered, "X-Razorpay-Event-Id": event_id},
    )

    assert resp.status_code in (400, 401)
    assert client.webhook_event_count(event_id) == 0


# ---- case 4: valid new event ----------------------------------------------


def test_valid_new_event_gets_durable_handoff_then_fast_2xx(client: WebhookTestClient) -> None:
    raw_body, signature, event_id = _valid_delivery(client)

    resp = client.http.post(
        WEBHOOK_PATH,
        content=raw_body,
        headers={"X-Razorpay-Signature": signature, "X-Razorpay-Event-Id": event_id},
    )

    assert 200 <= resp.status_code < 300
    assert client.webhook_event_count(event_id) == 1


# ---- case 5: valid duplicate event -----------------------------------------


def test_valid_duplicate_event_gets_fast_2xx_and_no_duplicate_processing(
    client: WebhookTestClient,
) -> None:
    raw_body, signature, event_id = _valid_delivery(client)
    headers = {"X-Razorpay-Signature": signature, "X-Razorpay-Event-Id": event_id}

    first = client.http.post(WEBHOOK_PATH, content=raw_body, headers=headers)
    second = client.http.post(WEBHOOK_PATH, content=raw_body, headers=headers)

    assert 200 <= first.status_code < 300
    assert 200 <= second.status_code < 300
    assert client.webhook_event_count(event_id) == 1  # exactly one row, never two


# ---- transient internal failure after valid verification ------------------


class _PoisonedWebhookEvents:
    """Simulates a real database error happening *after* signature
    verification has already succeeded, during the durable handoff."""

    async def claim(self, record: Any) -> bool:
        raise RuntimeError("simulated transient database failure")


class _PoisonedUnitOfWork:
    def __init__(self) -> None:
        self.webhook_events = _PoisonedWebhookEvents()

    async def commit(self) -> None:
        raise AssertionError("commit() is unreachable -- claim() must raise first")


def test_internal_failure_after_valid_verification_returns_non_2xx(
    postgres_url: str,
) -> None:
    """A valid signature that then fails during the durable handoff (a
    genuine transient database error) must never be reported as success --
    a non-2xx lets Razorpay's own retry policy recover it, rather than
    this system silently losing a delivery it claims to have accepted."""
    provider = SimulatorAdapter(clock=SystemClock())
    raw_body, signature, event_id = provider.build_webhook_payload(
        "payment.captured",
        provider_order_id="order_test",
        provider_payment_id="pay_test",
        amount_minor=100000,
    )

    async def _override_get_uow() -> AsyncIterator[UnitOfWork]:
        yield _PoisonedUnitOfWork()  # type: ignore[misc]

    def _override_get_provider() -> PaymentProvider:
        return provider

    app.dependency_overrides[get_uow] = _override_get_uow
    app.dependency_overrides[get_payment_provider] = _override_get_provider
    try:
        with TestClient(app, raise_server_exceptions=False) as http_client:
            resp = http_client.post(
                WEBHOOK_PATH,
                content=raw_body,
                headers={"X-Razorpay-Signature": signature, "X-Razorpay-Event-Id": event_id},
            )
    finally:
        app.dependency_overrides.pop(get_uow, None)
        app.dependency_overrides.pop(get_payment_provider, None)

    assert resp.status_code >= 500
