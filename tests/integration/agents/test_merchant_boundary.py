"""§28 P7 instruction 5: the merchant-agent security boundary. It loads
the mandate from its own database by `mandate_id` and compares the
stored `mandate_spec_hash`/derived `intent_hash` against what the buyer
claims -- it never accepts, parses, persists, or trusts a buyer-supplied
mandate body. There is no such body in the wire protocol at all (§8.4);
the attack this proves inert is a buyer claiming a *hash* that does not
match this merchant's own record, as if it had a modified, wider-cap
mandate.
"""

from __future__ import annotations

from actl.domain.mandate.state_machine import MandateStatus
from actl.infrastructure.db.uow import UnitOfWork
from actl.platform.ids import new_id
from tests.integration.agents.conftest import (
    AgentTestClient,
    build_signed_envelope,
    generate_test_identity,
)
from tests.integration.catalog.conftest import make_catalog_item
from tests.integration.db.conftest import make_locked_mandate

_MERCHANT = "agt_merchant_01"


def _seed(agent_client: AgentTestClient, *, sku: str) -> tuple[str, str, str]:
    """Seeds one mandate + one quote-able catalog item + issues a real
    quote through the normal protocol path. Returns (mandate_id, quote_id,
    quote_hash). A fresh buyer identity every call -- this shares a
    session-scoped Postgres container with every other test, so a fixed
    agent_id would collide on the second call."""
    buyer = generate_test_identity(new_id("agt_buyer_boundary"))
    agent_client.seed_identity(buyer)
    mandate = make_locked_mandate()
    item = make_catalog_item(sku, unit_price_minor=250000, available_units=5)

    async def _seed_db() -> None:
        async with UnitOfWork(agent_client.session_factory) as uow:
            await uow.mandates.add(mandate, MandateStatus.LOCKED)
            await uow.catalog.upsert_item(item)
            await uow.commit()

    assert agent_client.http.portal is not None
    agent_client.http.portal.call(_seed_db)

    quote_resp = agent_client.post_envelope(
        build_signed_envelope(
            buyer,
            to=_MERCHANT,
            type="quote.request",
            body={"sku": sku, "mandate_id": mandate.mandate_id, "nights": 3},
        )
    )
    quote_body = quote_resp.json()["body"]  # type: ignore[attr-defined]
    return mandate.mandate_id, quote_body["quote_id"], quote_body["quote_hash"]


def test_altered_mandate_spec_hash_is_rejected_never_widening_the_cap(
    agent_client: AgentTestClient,
) -> None:
    """The buyer claims a `mandate_spec_hash` that does not match this
    merchant's own stored mandate -- as it would if it had locally
    fabricated a mandate with a higher cap and were trying to smuggle that
    fabrication in by its hash alone. The merchant must reject this
    outright; it never even reaches the policy engine, let alone the
    gate."""
    buyer = generate_test_identity("agt_buyer_altered_hash")
    agent_client.seed_identity(buyer)
    mandate_id, quote_id, quote_hash = _seed(agent_client, sku="HTL-BOUNDARY-01")

    fabricated_hash = "sha256:" + "f" * 64  # a hash for a mandate that does not exist here

    response = agent_client.post_envelope(
        build_signed_envelope(
            buyer,
            to=_MERCHANT,
            type="order.propose",
            body={
                "quote_id": quote_id,
                "quote_hash": quote_hash,
                "mandate_id": mandate_id,
                "mandate_spec_hash": fabricated_hash,
                "intent_hash": "sha256:" + "a" * 64,
            },
        )
    )

    assert response.status_code == 200  # type: ignore[attr-defined]
    body = response.json()  # type: ignore[attr-defined]
    assert body["type"] == "order.propose"
    assert body["body"]["decision"] == "reject"
    assert body["body"]["reason_code"] == "MANDATE_TAMPERED"

    # No reservation, no order, no saga -- the fabrication influenced nothing.
    async def _assert_untouched() -> None:
        async with UnitOfWork(agent_client.session_factory) as uow:
            loaded = await uow.mandates.get(mandate_id)
        assert loaded is not None
        assert loaded[1] == MandateStatus.LOCKED  # never advanced to EXECUTING

    assert agent_client.http.portal is not None
    agent_client.http.portal.call(_assert_untouched)


def test_altered_intent_hash_is_rejected(agent_client: AgentTestClient) -> None:
    """The buyer claims an `intent_hash` that does not match what the
    merchant independently derives from its own quote + mandate -- as it
    would if it had computed the intent against different (higher) bounds
    than what this merchant's mandate actually grants."""
    buyer = generate_test_identity("agt_buyer_altered_intent")
    agent_client.seed_identity(buyer)
    mandate_id, quote_id, quote_hash = _seed(agent_client, sku="HTL-BOUNDARY-02")

    async def _get_mandate() -> str:
        async with UnitOfWork(agent_client.session_factory) as uow:
            loaded = await uow.mandates.get(mandate_id)
        assert loaded is not None
        return loaded[0].spec_hash or ""

    assert agent_client.http.portal is not None
    real_mandate_spec_hash = agent_client.http.portal.call(_get_mandate)

    response = agent_client.post_envelope(
        build_signed_envelope(
            buyer,
            to=_MERCHANT,
            type="order.propose",
            body={
                "quote_id": quote_id,
                "quote_hash": quote_hash,
                "mandate_id": mandate_id,
                "mandate_spec_hash": real_mandate_spec_hash,
                "intent_hash": "sha256:"
                + "b" * 64,  # fabricated, doesn't match the real quote/mandate
            },
        )
    )

    assert response.status_code == 200  # type: ignore[attr-defined]
    body = response.json()  # type: ignore[attr-defined]
    assert body["body"]["decision"] == "reject"
    assert body["body"]["reason_code"] == "INTENT_MISMATCH"


def test_altered_quote_hash_is_rejected(agent_client: AgentTestClient) -> None:
    """The buyer references a real quote_id and the real mandate_spec_hash
    but claims a fabricated quote_hash -- as it would if it had locally
    altered the price/terms it thinks it agreed to. Isolates the
    quote_hash check specifically: mandate identity passes, only the
    quote reference is tampered."""
    buyer = generate_test_identity("agt_buyer_altered_quote")
    agent_client.seed_identity(buyer)
    mandate_id, quote_id, _real_quote_hash = _seed(agent_client, sku="HTL-BOUNDARY-03")

    async def _get_mandate_spec_hash() -> str:
        async with UnitOfWork(agent_client.session_factory) as uow:
            loaded = await uow.mandates.get(mandate_id)
        assert loaded is not None
        return loaded[0].spec_hash or ""

    assert agent_client.http.portal is not None
    real_mandate_spec_hash = agent_client.http.portal.call(_get_mandate_spec_hash)

    response = agent_client.post_envelope(
        build_signed_envelope(
            buyer,
            to=_MERCHANT,
            type="order.propose",
            body={
                "quote_id": quote_id,
                "quote_hash": "sha256:" + "c" * 64,
                "mandate_id": mandate_id,
                "mandate_spec_hash": real_mandate_spec_hash,
                "intent_hash": "sha256:" + "e" * 64,
            },
        )
    )

    assert response.status_code == 200  # type: ignore[attr-defined]
    body = response.json()  # type: ignore[attr-defined]
    assert body["body"]["decision"] == "reject"
    assert body["body"]["reason_code"] == "STALE_PRICE"


def test_order_propose_for_an_unknown_mandate_is_rejected(agent_client: AgentTestClient) -> None:
    buyer = generate_test_identity("agt_buyer_unknown_mandate")
    agent_client.seed_identity(buyer)

    response = agent_client.post_envelope(
        build_signed_envelope(
            buyer,
            to=_MERCHANT,
            type="order.propose",
            body={
                "quote_id": "qte_does_not_exist",
                "quote_hash": "sha256:" + "0" * 64,
                "mandate_id": "mdt_does_not_exist",
                "mandate_spec_hash": "sha256:" + "0" * 64,
                "intent_hash": "sha256:" + "0" * 64,
            },
        )
    )

    assert response.status_code == 200  # type: ignore[attr-defined]
    body = response.json()  # type: ignore[attr-defined]
    assert body["body"]["decision"] == "reject"
    assert body["body"]["reason_code"] == "MANDATE_INVALID"


def test_order_propose_for_a_revoked_mandate_is_rejected(agent_client: AgentTestClient) -> None:
    buyer = generate_test_identity("agt_buyer_revoked_mandate")
    agent_client.seed_identity(buyer)
    mandate = make_locked_mandate()

    async def _seed_revoked() -> None:
        async with UnitOfWork(agent_client.session_factory) as uow:
            await uow.mandates.add(mandate, MandateStatus.REVOKED)
            await uow.commit()

    assert agent_client.http.portal is not None
    agent_client.http.portal.call(_seed_revoked)

    response = agent_client.post_envelope(
        build_signed_envelope(
            buyer,
            to=_MERCHANT,
            type="order.propose",
            body={
                "quote_id": "qte_does_not_exist",
                "quote_hash": "sha256:" + "0" * 64,
                "mandate_id": mandate.mandate_id,
                "mandate_spec_hash": mandate.spec_hash,
                "intent_hash": "sha256:" + "0" * 64,
            },
        )
    )

    assert response.status_code == 200  # type: ignore[attr-defined]
    body = response.json()  # type: ignore[attr-defined]
    assert body["body"]["decision"] == "reject"
    assert body["body"]["reason_code"] == "MANDATE_REVOKED"
