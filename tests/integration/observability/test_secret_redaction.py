"""§22 / §28 P10 instruction 4: a unique, greppable canary value in every
secret-bearing setting, then a full transaction -- simulator/provider
(happy_path), LLM fallback (llm_down uses `NullLLMClient`, never real
Groq -- demo.py's own docstring), webhook delivery, worker ticks
(webhook processing, reconciliation, sweep), audit appends, and
failure/compensation paths (over_cap, stale_price, declined) -- proving
no canary substring ever reaches a log line, a span, a metric, or an
HTTP API response.

§16.1/§22's own design is *why* this test can be this thorough: nothing
in `application`/`infrastructure` logs free-text business data (grep
confirms only worker.py/main.py/the razorpay adapter call a logger at
all, and none of those interpolate a settings value), spans only ever
carry the deliberately narrow attributes this build's own P10 span-
wiring pass added, and audit payloads are typed dicts of ids/amounts/
statuses by construction -- this test is what turns "should be safe" into
a verified property, and would fail loudly the day any of that changes.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from actl.application import ledger_service
from actl.application.demo import SCENARIOS, run_scenario
from actl.application.payment_service import (
    process_unprocessed_webhooks,
    process_webhook_delivery,
    reconcile_non_terminal_orders,
)
from actl.config import settings
from actl.infrastructure.db.uow import UnitOfWork
from actl.infrastructure.providers.simulator.adapter import SimulatorAdapter
from actl.interfaces.http.deps import get_uow
from actl.main import app
from actl.platform import metrics
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import SystemClock
from actl.platform.tracing import configure_tracing

_NONCE = secrets.token_hex(12)

_CANARY_SETTINGS: dict[str, str] = {
    "razorpay_key_id": f"rzp_test_CANARY{_NONCE}",
    "razorpay_key_secret": f"CANARY{_NONCE}razorpaysecret",
    "razorpay_webhook_secret": f"CANARY{_NONCE}webhooksecret",
    "groq_api_key": f"CANARY{_NONCE}groqkey",
    "quote_signing_key": f"CANARY{_NONCE}quotesigning",
    "mandate_signing_key": f"CANARY{_NONCE}mandatesigning",
    "admin_token": f"CANARY{_NONCE}admintoken",
    "read_token": f"CANARY{_NONCE}readtoken",
    "merchant_private_key_hex": f"CANARY{_NONCE}merchantkey",
}


@dataclass
class RedactionTestClient:
    http: TestClient
    session_factory: async_sessionmaker[AsyncSession]
    setup_phase_stdout: str


@pytest.fixture
def redaction_client(
    postgres_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> Iterator[RedactionTestClient]:
    for field, value in _CANARY_SETTINGS.items():
        monkeypatch.setattr(settings, field, value)

    test_engine = create_async_engine(postgres_url, pool_size=5, max_overflow=10)
    test_session_factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def _override_get_uow() -> AsyncIterator[UnitOfWork]:
        async with UnitOfWork(test_session_factory) as uow:
            yield uow

    app.dependency_overrides[get_uow] = _override_get_uow
    try:
        # `capsys.readouterr()` only drains whatever was printed since the
        # last read *within the same pytest capture phase* -- fixture
        # setup is a separate phase from the test body's "call" phase, so
        # `TestClient.__enter__`'s lifespan startup log line (`app.
        # startup`) would otherwise be invisible to a `capsys.readouterr()`
        # called from inside the test function. Draining it here, in the
        # same phase it was written, and handing the text to the test
        # body is what actually closes that gap.
        with TestClient(app) as http_client:
            setup_phase_stdout = capsys.readouterr().out
            yield RedactionTestClient(
                http=http_client,
                session_factory=test_session_factory,
                setup_phase_stdout=setup_phase_stdout,
            )
    finally:
        app.dependency_overrides.pop(get_uow, None)


async def _run_worker_ticks_and_webhook(
    session_factory: async_sessionmaker[AsyncSession], happy_path_order_id: str
) -> None:
    clock = SystemClock()
    provider = SimulatorAdapter(clock=clock)
    breaker = CircuitBreaker(name="redaction-e2e", clock=clock)

    async with UnitOfWork(session_factory) as uow:
        order = await uow.orders.get(happy_path_order_id)
    assert order is not None
    if order.provider_order_id is not None:
        payments = await provider.fetch_payments(order.provider_order_id)
        if payments:
            raw_body, signature, event_id = provider.build_webhook_payload(
                "payment.captured",
                provider_order_id=order.provider_order_id,
                provider_payment_id=payments[0].id,
                amount_minor=order.amount_minor,
            )
            async with UnitOfWork(session_factory) as uow:
                await process_webhook_delivery(
                    uow,
                    provider,
                    raw_body=raw_body,
                    signature=signature,
                    event_id=event_id,
                    event_type="payment.captured",
                    payload=json.loads(raw_body),
                )
                await uow.commit()

    async with UnitOfWork(session_factory) as uow:
        await process_unprocessed_webhooks(uow, clock)
        await uow.commit()
    async with UnitOfWork(session_factory) as uow:
        await reconcile_non_terminal_orders(uow, provider, clock, breaker)
        await uow.commit()
    async with UnitOfWork(session_factory) as uow:
        await ledger_service.sweep(uow, clock, reservation_ttl_s=settings.reservation_ttl_s)
        await uow.commit()


async def _order_id_from_trace(
    session_factory: async_sessionmaker[AsyncSession], trace_id: str
) -> str | None:
    async with UnitOfWork(session_factory) as uow:
        entries = await uow.audit_log.get_by_trace_id(trace_id)
    for entry in entries:
        order_id = entry.subject.get("order_id")
        if isinstance(order_id, str):
            return order_id
    return None


def test_no_canary_secret_leaks_across_a_full_transaction(
    redaction_client: RedactionTestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    assert redaction_client.http.portal is not None
    portal = redaction_client.http.portal
    session_factory = redaction_client.session_factory

    exporter = InMemorySpanExporter()
    configure_tracing(exporter)

    order_ids: dict[str, str] = {}
    for scenario in SCENARIOS:  # happy_path, over_cap, stale_price, declined, llm_down
        result = portal.call(run_scenario, scenario, session_factory)
        found = portal.call(_order_id_from_trace, session_factory, result.trace_id)
        if found is not None:
            order_ids[scenario] = found

    # Sanity: at least the scenarios that actually create an order
    # (happy_path always captures/settles; declined creates then declines)
    # must have been found -- an empty dict here would mean the rest of
    # this test silently skips its HTTP-response-body coverage instead of
    # proving anything.
    assert "happy_path" in order_ids, order_ids
    assert "declined" in order_ids, order_ids

    portal.call(_run_worker_ticks_and_webhook, session_factory, order_ids["happy_path"])

    http_response_bodies: list[str] = []
    for order_id in order_ids.values():
        response = redaction_client.http.get(
            f"/audit/explain/{order_id}",
            headers={"Authorization": f"Bearer {settings.read_token}"},
        )
        http_response_bodies.append(response.text)

    admin_response = redaction_client.http.post(
        "/admin/catalog/HTL-DEMO-HAPPY/price",
        json={"unit_price_minor": 123456},
        headers={"Authorization": f"Bearer {settings.admin_token}"},
    )
    http_response_bodies.append(admin_response.text)

    unauthorized_response = redaction_client.http.get(
        "/audit/explain/ord_nonexistent", headers={"Authorization": "Bearer wrong-token"}
    )
    http_response_bodies.append(unauthorized_response.text)

    metrics_response = redaction_client.http.get("/metrics")
    http_response_bodies.append(metrics_response.text)
    metrics_text = metrics.render().decode("utf-8")

    captured_stdout = redaction_client.setup_phase_stdout + capsys.readouterr().out

    spans = exporter.get_finished_spans()
    span_text = "\n".join(f"{s.name} {dict(s.attributes or {})}" for s in spans)

    all_surfaces = "\n".join([captured_stdout, span_text, metrics_text, *http_response_bodies])

    for field, canary in _CANARY_SETTINGS.items():
        assert canary not in all_surfaces, f"{field}'s canary value leaked: {canary!r}"

    # Sanity: the canaries were genuinely wired in and actually
    # authenticated real requests -- a passing sweep over settings nobody
    # ever used would be a false proof of safety, not a real one.
    assert admin_response.status_code == 200
    assert unauthorized_response.status_code == 401
