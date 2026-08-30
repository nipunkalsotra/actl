"""§22 / §28 P10 exit criterion: GET /audit/explain/{order_id} end to end
-- a full transaction (quote -> propose -> capture -> settle -> webhook),
through the exact same application code real traffic uses
(`catalog_service.create_quote`, `merchant.handle_order_propose`,
`saga.complete_purchase`, `payment_service.process_webhook_delivery`),
then the HTTP endpoint itself validates ordering, hashes, auth, and that
no secret ever reaches the response body.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from actl.application.agents.merchant import handle_order_propose
from actl.application.catalog_service import create_quote
from actl.application.orchestrator import saga
from actl.application.payment_service import process_webhook_delivery
from actl.config import settings
from actl.domain.mandate.state_machine import MandateStatus
from actl.domain.policy.rules import PurchaseIntent, compute_intent_hash
from actl.infrastructure.db.repositories.catalog import CatalogItemRecord
from actl.infrastructure.db.uow import UnitOfWork
from actl.infrastructure.providers.simulator.adapter import SimulatorAdapter
from actl.interfaces.http.deps import get_uow
from actl.main import app
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import SystemClock
from actl.platform.ids import new_id
from tests.integration.db.conftest import make_locked_mandate

_SKU = "HTL-EXPLAIN-E2E"


@dataclass
class ExplainTestClient:
    http: TestClient
    session_factory: async_sessionmaker[AsyncSession]


@pytest.fixture
def explain_client(postgres_url: str) -> Iterator[ExplainTestClient]:
    test_engine = create_async_engine(postgres_url, pool_size=5, max_overflow=10)
    test_session_factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def _override_get_uow() -> AsyncIterator[UnitOfWork]:
        async with UnitOfWork(test_session_factory) as uow:
            yield uow

    app.dependency_overrides[get_uow] = _override_get_uow
    try:
        with TestClient(app) as http_client:
            yield ExplainTestClient(http=http_client, session_factory=test_session_factory)
    finally:
        app.dependency_overrides.pop(get_uow, None)


async def _run_full_transaction(session_factory: async_sessionmaker[AsyncSession]) -> str:
    mandate = make_locked_mandate()
    clock = SystemClock()
    provider = SimulatorAdapter(clock=clock)
    breaker = CircuitBreaker(name="explain-e2e", clock=clock)
    actor_id = "agt_explain_e2e"

    async with UnitOfWork(session_factory) as uow:
        await uow.mandates.add(mandate, MandateStatus.LOCKED)
        current_version = await uow.catalog.current_version()
        await uow.catalog.upsert_item(
            CatalogItemRecord(
                sku=_SKU,
                category="travel.hotel",
                merchant_id="mrc_explain_e2e",
                unit="night",
                unit_price_minor=280000,
                available_units=5,
                location_city="Goa",
                location_country="IN",
                rating=4.2,
                sea_facing=True,
                breakfast_included=True,
                refundable=True,
                cancellation_window_h=48,
                instant_confirm=True,
                taxes_included=True,
                quote_required=True,
                version=current_version,
            )
        )
        await uow.commit()

    async with UnitOfWork(session_factory) as uow:
        quote = await create_quote(
            uow, clock, mandate_id=mandate.mandate_id, sku=_SKU, nights=3, actor_id=actor_id
        )
        await uow.commit()
        item = await uow.catalog.get_item(_SKU)
    assert item is not None

    intent_draft = PurchaseIntent(
        currency=mandate.bounds.currency,
        category=item.category,
        merchant=item.merchant_id,
        unit_price_minor=quote.unit_price_minor,
        total_minor=quote.total_minor,
        nights=quote.nights,
        rooms=mandate.intent.rooms,
        refundable=quote.refundable,
        quoted_total_minor=quote.total_minor,
        current_total_minor=item.unit_price_minor * quote.nights,
        catalog_version=quote.catalog_version,
        mandate_spec_hash=mandate.spec_hash or "",
        intent_hash="",
    )
    intent_hash = compute_intent_hash(intent_draft)

    outcome = await handle_order_propose(
        session_factory,
        provider,
        clock,
        breaker,
        quote_id=quote.quote_id,
        quote_hash=quote.quote_hash or "",
        mandate_id=mandate.mandate_id,
        mandate_spec_hash=mandate.spec_hash or "",
        intent_hash=intent_hash,
        trace_id=new_id("corr"),
        actor_id=actor_id,
    )
    assert outcome.body.get("decision") == "accept", outcome.body
    order_id = str(outcome.body["order_id"])
    saga_id = str(outcome.body["saga_id"])

    async with UnitOfWork(session_factory) as uow:
        order = await uow.orders.get(order_id)
    assert order is not None
    assert order.provider_order_id is not None
    payments = await provider.fetch_payments(order.provider_order_id)
    payment = payments[0]
    checkout_signature = provider.build_checkout_payload(order.provider_order_id, payment.id)

    result = await saga.complete_purchase(
        saga_id,
        session_factory,
        provider,
        clock,
        breaker,
        provider_order_id=order.provider_order_id,
        provider_payment_id=payment.id,
        provider_signature=checkout_signature,
    )
    assert result.status == "COMPLETED"

    raw_body, webhook_signature, event_id = provider.build_webhook_payload(
        "payment.captured",
        provider_order_id=order.provider_order_id,
        provider_payment_id=payment.id,
        amount_minor=order.amount_minor,
    )
    async with UnitOfWork(session_factory) as uow:
        await process_webhook_delivery(
            uow,
            provider,
            raw_body=raw_body,
            signature=webhook_signature,
            event_id=event_id,
            event_type="payment.captured",
            payload=json.loads(raw_body),
        )
        await uow.commit()

    return order_id


_EXPECTED_ACTIONS = {
    "mandate.locked",
    "quote.issued",
    "order.proposed",
    "policy.decision",
    "budget.reserved",
    "payment.intent",
    "payment.result",
    "webhook.received",
    "settlement.closed",
}


def test_explain_endpoint_returns_ordered_timeline_with_hashes(
    explain_client: ExplainTestClient,
) -> None:
    assert explain_client.http.portal is not None
    order_id = explain_client.http.portal.call(
        _run_full_transaction, explain_client.session_factory
    )

    response = explain_client.http.get(
        f"/audit/explain/{order_id}",
        headers={"Authorization": f"Bearer {settings.read_token}"},
    )
    assert response.status_code == 200
    body = response.json()

    assert body["order_id"] == order_id
    assert body["terminal_outcome"]["status"] == "CAPTURED"

    timeline = body["timeline"]
    assert len(timeline) >= len(_EXPECTED_ACTIONS)
    actions = {item["action"] for item in timeline}
    assert actions >= _EXPECTED_ACTIONS, _EXPECTED_ACTIONS - actions

    # Stable ordering: every timestamp is non-decreasing across the timeline.
    timestamps = [item["ts"] for item in timeline]
    assert timestamps == sorted(timestamps)

    # audit_log-sourced items carry all three hashes; synthesized items
    # (mandate.locked, policy.decision, webhook.received) carry none --
    # never a fabricated hash standing in for a real chain entry.
    audit_log_actions = {
        "order.proposed",
        "quote.issued",
        "budget.reserved",
        "payment.intent",
        "payment.result",
        "settlement.closed",
    }
    synthesized_actions = {"mandate.locked", "policy.decision", "webhook.received"}
    for item in timeline:
        if item["action"] in audit_log_actions:
            assert item["hashes"]["entry_hash"] is not None, item
            assert item["hashes"]["prev_hash"] is not None, item
            assert item["hashes"]["payload_hash"] is not None, item
            assert item["seq"] is not None, item
            assert item["type"] in ("fact", "decision", "provider_event", "compensation")
        elif item["action"] in synthesized_actions:
            assert item["hashes"]["entry_hash"] is None, item
            assert item["seq"] is None, item

    # Never a secret in the response body -- the read token that
    # authenticated this very request, the admin token, and every
    # signing/HMAC key this build holds.
    raw_text = response.text
    for secret in (
        settings.read_token,
        settings.admin_token,
        settings.mandate_signing_key,
        settings.quote_signing_key,
    ):
        assert secret not in raw_text


def test_explain_endpoint_requires_read_token(explain_client: ExplainTestClient) -> None:
    response = explain_client.http.get("/audit/explain/ord_does_not_exist")
    assert response.status_code == 401

    response = explain_client.http.get(
        "/audit/explain/ord_does_not_exist", headers={"Authorization": "Bearer wrong-token"}
    )
    assert response.status_code == 401


def test_explain_endpoint_returns_typed_not_found(explain_client: ExplainTestClient) -> None:
    response = explain_client.http.get(
        "/audit/explain/ord_does_not_exist",
        headers={"Authorization": f"Bearer {settings.read_token}"},
    )
    assert response.status_code == 404
    body = response.json()
    assert body["detail"]["reason_code"] == "ORDER_NOT_FOUND"
    assert body["detail"]["order_id"] == "ord_does_not_exist"
