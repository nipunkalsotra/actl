"""§28 P7 instruction 4: all seven §14 message types exercised end to
end, through the real signed-envelope pipeline, over a real Postgres +
Redis + the deterministic SimulatorAdapter -- never LLM, never a real
Razorpay call.

§14's table lists exactly these seven, verbatim: capability.discover,
catalog.query, quote.request, order.propose, order.status, receipt.issue,
error. "error" is response-only (§14: "either" direction, no further
response) -- it is not a message a buyer ever sends, so it is exercised
here as the *response* to the early (not-yet-settled) receipt.issue call
at step 6, not as an eighth request. `_SEVEN_MESSAGE_TYPES` is checked
against the domain model's own `REQUEST_MESSAGE_TYPES | {"error"}` so this
test's notion of "seven" can never silently drift from the wire format's.
"""

from __future__ import annotations

from actl.application.orchestrator import saga
from actl.domain.agent.envelope import REQUEST_MESSAGE_TYPES
from actl.domain.mandate.state_machine import MandateStatus
from actl.domain.policy.rules import PurchaseIntent, compute_intent_hash
from actl.infrastructure.db.uow import UnitOfWork
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import SystemClock
from tests.integration.agents.conftest import (
    AgentTestClient,
    build_signed_envelope,
    generate_test_identity,
)
from tests.integration.catalog.conftest import make_catalog_item
from tests.integration.db.conftest import make_locked_mandate

_MERCHANT = "agt_merchant_01"

# §14's message table, verbatim, in the order it lists them.
_SEVEN_MESSAGE_TYPES = frozenset(
    {
        "capability.discover",
        "catalog.query",
        "quote.request",
        "order.propose",
        "order.status",
        "receipt.issue",
        "error",
    }
)


def test_seven_message_types_match_the_domain_models_own_set() -> None:
    """Ties this test file's literal §14 list back to the one already
    enforced by `AgentEnvelope.type` (`domain/agent/envelope.py`'s
    `MessageType`/`REQUEST_MESSAGE_TYPES`), so the two can never quietly
    diverge."""
    assert REQUEST_MESSAGE_TYPES | {"error"} == _SEVEN_MESSAGE_TYPES
    assert len(_SEVEN_MESSAGE_TYPES) == 7


def test_all_seven_message_types_end_to_end(agent_client: AgentTestClient) -> None:
    buyer = generate_test_identity("agt_buyer_e2e")
    agent_client.seed_identity(buyer)
    sku = "HTL-E2E-01"
    mandate = make_locked_mandate()
    item = make_catalog_item(sku, unit_price_minor=250000, available_units=5)
    seen_types: set[str] = set()

    async def _seed() -> None:
        async with UnitOfWork(agent_client.session_factory) as uow:
            await uow.mandates.add(mandate, MandateStatus.LOCKED)
            await uow.catalog.upsert_item(item)
            await uow.commit()

    assert agent_client.http.portal is not None
    agent_client.http.portal.call(_seed)

    # 1. capability.discover
    discover = agent_client.post_envelope(
        build_signed_envelope(
            buyer,
            to=_MERCHANT,
            type="capability.discover",
            body={"supported_protocols": ["actl.acp/1"]},
        )
    )
    assert discover.status_code == 200, discover.text  # type: ignore[attr-defined]
    discover_envelope = discover.json()  # type: ignore[attr-defined]
    seen_types.add(discover_envelope["type"])
    discover_body = discover_envelope["body"]
    assert discover_body["protocol"] == "actl.acp/1"
    assert "/agent/v1/messages" in discover_body["endpoints"]["messages"]

    # 2. catalog.query
    catalog_resp = agent_client.post_envelope(
        build_signed_envelope(
            buyer,
            to=_MERCHANT,
            type="catalog.query",
            body={"category": "travel.hotel", "limit": 50},
        )
    )
    assert catalog_resp.status_code == 200, catalog_resp.text  # type: ignore[attr-defined]
    catalog_envelope = catalog_resp.json()  # type: ignore[attr-defined]
    seen_types.add(catalog_envelope["type"])
    catalog_body = catalog_envelope["body"]
    skus = {i["sku"] for i in catalog_body["items"]}
    assert sku in skus
    live_item = next(i for i in catalog_body["items"] if i["sku"] == sku)

    # 3. quote.request
    quote_resp = agent_client.post_envelope(
        build_signed_envelope(
            buyer,
            to=_MERCHANT,
            type="quote.request",
            body={"sku": sku, "mandate_id": mandate.mandate_id, "nights": 3},
        )
    )
    assert quote_resp.status_code == 200, quote_resp.text  # type: ignore[attr-defined]
    quote_envelope = quote_resp.json()  # type: ignore[attr-defined]
    seen_types.add(quote_envelope["type"])
    quote_body = quote_envelope["body"]
    assert quote_body["total_minor"] == 250000 * 3

    # 4. order.propose -- the buyer independently derives the same
    # intent_hash the merchant will, from the same quote + mandate +
    # catalog facts it already has (§8.4 WHY THIS WAY).
    intent_draft = PurchaseIntent(
        currency=mandate.bounds.currency,
        category=live_item["category"],
        merchant=live_item["merchant_id"],
        unit_price_minor=quote_body["unit_price_minor"],
        total_minor=quote_body["total_minor"],
        nights=quote_body["nights"],
        rooms=mandate.intent.rooms,
        refundable=quote_body["refundable"],
        quoted_total_minor=quote_body["total_minor"],
        current_total_minor=live_item["unit_price_minor"] * quote_body["nights"],
        catalog_version=quote_body["catalog_version"],
        mandate_spec_hash=mandate.spec_hash or "",
        intent_hash="",
    )
    intent_hash = compute_intent_hash(intent_draft)

    propose_resp = agent_client.post_envelope(
        build_signed_envelope(
            buyer,
            to=_MERCHANT,
            type="order.propose",
            body={
                "quote_id": quote_body["quote_id"],
                "quote_hash": quote_body["quote_hash"],
                "mandate_id": mandate.mandate_id,
                "mandate_spec_hash": mandate.spec_hash,
                "intent_hash": intent_hash,
            },
        )
    )
    assert propose_resp.status_code == 200, propose_resp.text  # type: ignore[attr-defined]
    propose_envelope = propose_resp.json()  # type: ignore[attr-defined]
    seen_types.add(propose_envelope["type"])
    propose_body = propose_envelope["body"]
    assert propose_body["decision"] == "accept", propose_body
    order_id = propose_body["order_id"]
    saga_id = propose_body["saga_id"]

    # 5. order.status -- pending checkout authorization at this point
    # (§15.4: capture requires the payer's own, later authorization).
    status_resp = agent_client.post_envelope(
        build_signed_envelope(buyer, to=_MERCHANT, type="order.status", body={"order_id": order_id})
    )
    assert status_resp.status_code == 200, status_resp.text  # type: ignore[attr-defined]
    status_envelope = status_resp.json()  # type: ignore[attr-defined]
    seen_types.add(status_envelope["type"])
    status_body = status_envelope["body"]
    assert status_body["order_id"] == order_id
    assert status_body["status"] == "CREATED"
    assert status_body["audit_seq_from"] is not None
    assert status_body["audit_seq_to"] is not None

    # 6. receipt.issue -- not yet settled: a typed, retryable rejection,
    # never a false receipt.
    early_receipt = agent_client.post_envelope(
        build_signed_envelope(
            buyer, to=_MERCHANT, type="receipt.issue", body={"order_id": order_id}
        )
    )
    assert early_receipt.status_code == 200  # type: ignore[attr-defined]
    early_receipt_envelope = early_receipt.json()  # type: ignore[attr-defined]
    assert early_receipt_envelope["type"] == "error"
    seen_types.add(early_receipt_envelope["type"])
    assert early_receipt_envelope["body"]["reason_code"] == "ORDER_NOT_SETTLED"
    assert early_receipt_envelope["body"]["retryable"] is True

    # Drive the saga to settlement the same way P6's own tests do -- there
    # is no checkout-callback message type in §14 (out of scope until the
    # real Checkout UI, §15.4); this models "the browser returns" using
    # the SimulatorAdapter's deterministic equivalent. Reuses the *same*
    # adapter instance the route's own order.propose call used (it holds
    # in-memory order/payment state, keyed per instance) -- never a fresh
    # one, which would know nothing about this order.
    provider = agent_client.provider
    breaker = CircuitBreaker(name="razorpay", clock=SystemClock())

    async def _settle() -> None:
        async with UnitOfWork(agent_client.session_factory) as uow:
            order = await uow.orders.get(order_id)
        assert order is not None
        assert order.provider_order_id is not None
        payments = await provider.fetch_payments(order.provider_order_id)
        payment = payments[0]
        signature = provider.build_checkout_payload(order.provider_order_id, payment.id)
        result = await saga.complete_purchase(
            saga_id,
            agent_client.session_factory,
            provider,
            SystemClock(),
            breaker,
            provider_order_id=order.provider_order_id,
            provider_payment_id=payment.id,
            provider_signature=signature,
        )
        assert result.status == "COMPLETED"

    assert agent_client.http.portal is not None
    agent_client.http.portal.call(_settle)

    # 7. receipt.issue -- now settled, a real signed receipt.
    receipt_resp = agent_client.post_envelope(
        build_signed_envelope(
            buyer, to=_MERCHANT, type="receipt.issue", body={"order_id": order_id}
        )
    )
    assert receipt_resp.status_code == 200, receipt_resp.text  # type: ignore[attr-defined]
    receipt_envelope = receipt_resp.json()  # type: ignore[attr-defined]
    assert receipt_envelope["type"] == "receipt.issue"
    seen_types.add(receipt_envelope["type"])
    assert receipt_envelope["sig"]["alg"] == "Ed25519"
    assert receipt_envelope["body"]["order_id"] == order_id
    assert receipt_envelope["body"]["amount_minor"] == 250000 * 3

    # All seven §14 message types, by name, distinct -- capability.discover,
    # catalog.query, quote.request, order.propose, order.status,
    # receipt.issue (both its "error" response while unsettled and its
    # real "receipt.issue" response once settled) each appeared exactly
    # once above.
    assert seen_types == _SEVEN_MESSAGE_TYPES
    assert len(seen_types) == 7
    assert receipt_envelope["body"]["payment_id"] is not None
