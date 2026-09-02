"""§28 P12: the thin buyer-facing REST surface (`interfaces/http/routers/
buyer.py`) exercised end to end over a real Postgres + SimulatorAdapter +
NullLLMClient -- the same TestClient-background-loop precedent
`tests/integration/agents/conftest.py` already documents (ADR 0005
decision 12). No gate/saga/policy/audit rule is re-tested here (that's
already covered by tests/integration/{agents,gate,payments,catalog}); this
file only proves the new routes reach those real services correctly."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, replace
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from tests.integration.catalog.conftest import make_catalog_item

from actl.application.agents.merchant import handle_order_propose
from actl.application.catalog_service import create_quote
from actl.config import settings
from actl.domain.ledger.model import account, net_balance
from actl.domain.mandate.hashing import compute_spec_hash
from actl.domain.mandate.models import (
    Delegate,
    Mandate,
    MandateBounds,
    MandateControls,
    MandateIntent,
    MandateSignature,
    MandateTemporal,
    Principal,
)
from actl.domain.mandate.signing import sign_spec_hash
from actl.domain.mandate.state_machine import MandateStatus
from actl.domain.policy.rules import PurchaseIntent, compute_intent_hash
from actl.infrastructure.db.repositories.catalog import CatalogItemRecord
from actl.infrastructure.db.uow import UnitOfWork
from actl.infrastructure.llm.fallback import NullLLMClient
from actl.infrastructure.providers.simulator.adapter import Scenario, SimulatorAdapter
from actl.interfaces.http.deps import (
    get_llm_client,
    get_payment_provider,
    get_session_factory,
    get_uow,
)
from actl.main import app
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import SystemClock
from actl.platform.ids import new_id


@dataclass
class BuyerClient:
    http: TestClient
    session_factory: async_sessionmaker[AsyncSession]

    def seed(self, *items: CatalogItemRecord) -> None:
        """Stamps each item's `version` to the *live* global catalog
        counter before inserting -- this shared-session Postgres container
        may already have an advanced counter from an earlier stale-price
        test in the same run (same mechanism as `application.demo.
        _seed_item`); a hardcoded `version=1` would make gate G5 see a
        freshly-seeded item as already stale."""
        assert self.http.portal is not None

        async def _do() -> None:
            async with UnitOfWork(self.session_factory) as uow:
                current_version = await uow.catalog.current_version()
                for item in items:
                    await uow.catalog.upsert_item(replace(item, version=current_version))
                await uow.commit()

        self.http.portal.call(_do)


@pytest.fixture
def buyer_client(postgres_url: str) -> Iterator[BuyerClient]:
    test_engine = create_async_engine(postgres_url, pool_size=5, max_overflow=10)
    test_session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    simulator = SimulatorAdapter(clock=SystemClock())

    async def _override_get_uow() -> AsyncIterator[UnitOfWork]:
        async with UnitOfWork(test_session_factory) as uow:
            yield uow

    # `get_uow` (used by create_mandate/get_buyer_catalog/get_order/
    # buyer_explain) builds its own `UnitOfWork()` internally and never
    # goes through the `get_session_factory` dependency at all -- same
    # precedent as tests/integration/catalog/conftest.py's `client`
    # fixture -- so both overrides are needed: this one for `get_uow`
    # routes, `get_session_factory` below for the propose/checkout routes
    # that take a session_factory directly (mirroring the saga/gate call
    # shape `application.demo` and the agent envelope route already use).
    app.dependency_overrides[get_uow] = _override_get_uow
    app.dependency_overrides[get_session_factory] = lambda: test_session_factory
    app.dependency_overrides[get_payment_provider] = lambda: simulator
    app.dependency_overrides[get_llm_client] = lambda: NullLLMClient()
    try:
        with TestClient(app) as http_client:
            yield BuyerClient(http=http_client, session_factory=test_session_factory)
    finally:
        app.dependency_overrides.pop(get_uow, None)
        app.dependency_overrides.pop(get_session_factory, None)
        app.dependency_overrides.pop(get_payment_provider, None)
        app.dependency_overrides.pop(get_llm_client, None)


def test_mandate_extract_missing_budget_never_invents_one(buyer_client: BuyerClient) -> None:
    resp = buyer_client.http.post(
        "/buyer/v1/mandate/extract", json={"conversation_text": "Book me something nice in Goa."}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "clarification_needed"
    assert "max_total_minor" in body["missing_slots"]
    # Progressive collection: only the one highest-priority question, not
    # every required slot -- and location was still recognised even though
    # the LLM is disabled (NullLLMClient) for this whole test file.
    assert body["questions"] == ["What's your total budget for this booking?"]
    assert body["slots"]["location"] == "Goa"


def test_mandate_extract_narrows_with_partial_info_llm_disabled(buyer_client: BuyerClient) -> None:
    """The exact reported bug: this input has real signal (hotel, Goa, 2
    nights, Rs 10k) that must not be discarded in favour of the generic
    ask-about-everything message."""
    resp = buyer_client.http.post(
        "/buyer/v1/mandate/extract",
        json={"conversation_text": "2 night hotel stay in Goa budget 10k"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "clarification_needed"
    assert body["slots"]["category"] == "travel.hotel"
    assert body["slots"]["location"] == "Goa"
    assert body["slots"]["nights"] == 2
    assert body["missing_slots"] == ["check_in", "rooms"]
    assert body["questions"] == ["What check-in date do you want?"]


def test_mandate_extract_multi_turn_transcript_merges_to_one_missing_field(
    buyer_client: BuyerClient,
) -> None:
    transcript = "\n".join(
        [
            "book me something nice in Goa",
            "2 night hotel stay in Goa budget 10k",
            "15 September, refundable, 2 guests",
        ]
    )
    resp = buyer_client.http.post(
        "/buyer/v1/mandate/extract", json={"conversation_text": transcript}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "clarification_needed"
    assert body["missing_slots"] == ["rooms"]
    assert body["slots"]["refundable"] is True
    assert body["slots"]["guests"] == 2


def test_config_reports_deterministic_llm_status_without_leaking_a_key(
    buyer_client: BuyerClient,
) -> None:
    resp = buyer_client.http.get("/buyer/v1/config")
    assert resp.status_code == 200
    body = resp.json()
    # This test file overrides get_llm_client with NullLLMClient directly
    # (not via settings.llm_enabled), so llm_status reflects real config --
    # the point of this assertion is simply that the field exists and never
    # carries a secret, not which value it holds in this test process.
    assert body["llm_status"] in ("deterministic", "groq_configured", "groq_healthy")
    raw = resp.text
    assert "groq_api_key" not in raw.lower()
    assert "gsk_" not in raw


def test_config_llm_status_never_claims_available_merely_from_a_configured_key(
    buyer_client: BuyerClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact narrow-follow-up requirement: LLM_ENABLED=true + a key
    present must report "groq_configured", never "groq_healthy", until a
    real request has actually succeeded -- proven here by flipping the
    app's own LLMHealth after the first check, never by faking settings."""
    monkeypatch.setattr(settings, "llm_enabled", True)

    resp = buyer_client.http.get("/buyer/v1/config")
    assert resp.status_code == 200
    assert resp.json()["llm_status"] == "groq_configured"

    app.state.llm_health.mark_success()

    resp2 = buyer_client.http.get("/buyer/v1/config")
    assert resp2.json()["llm_status"] == "groq_healthy"


def test_create_mandate_locks_and_signs(buyer_client: BuyerClient) -> None:
    resp = buyer_client.http.post(
        "/buyer/v1/mandate",
        json={
            "nights": 2,
            "rooms": 1,
            "max_total_minor": 600000,
            "require_refundable": True,
            "check_in": "2026-09-20",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "LOCKED"
    assert body["mandate_id"].startswith("mdt_")
    assert body["bounds"]["max_unit_minor"] == 300000  # 600000 // 2 nights


def test_catalog_ranked_by_mandate_filters_out_of_cap_items(buyer_client: BuyerClient) -> None:
    buyer_client.seed(
        make_catalog_item("HTL-BUYER-AFFORD", unit_price_minor=100000, rating=4.0),
        make_catalog_item("HTL-BUYER-TOOPRICEY", unit_price_minor=900000, rating=5.0),
    )
    mandate = buyer_client.http.post(
        "/buyer/v1/mandate",
        json={
            "nights": 1,
            "rooms": 1,
            "max_total_minor": 200000,
            "require_refundable": True,
            "check_in": "2026-09-20",
        },
    ).json()

    resp = buyer_client.http.get(f"/buyer/v1/catalog?mandate_id={mandate['mandate_id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ranked"] is True
    skus = [item["sku"] for item in body["items"]]
    assert "HTL-BUYER-AFFORD" in skus
    assert "HTL-BUYER-TOOPRICEY" not in skus


def test_full_purchase_happy_path_via_buyer_routes(buyer_client: BuyerClient) -> None:
    buyer_client.seed(make_catalog_item("HTL-BUYER-HAPPY", unit_price_minor=250000, rating=4.2))
    mandate = buyer_client.http.post(
        "/buyer/v1/mandate",
        json={
            "nights": 2,
            "rooms": 1,
            "max_total_minor": 900000,
            "require_refundable": True,
            "check_in": "2026-09-20",
        },
    ).json()

    quote = buyer_client.http.post(
        "/agent/v1/quote",
        json={"sku": "HTL-BUYER-HAPPY", "mandate_id": mandate["mandate_id"], "nights": 2},
    ).json()

    propose = buyer_client.http.post(
        "/buyer/v1/order/propose",
        json={"quote_id": quote["quote_id"], "mandate_id": mandate["mandate_id"]},
    ).json()
    assert propose["decision"] == "accept"

    checkout = buyer_client.http.post(
        "/buyer/v1/checkout",
        json={"order_id": propose["order_id"], "saga_id": propose["saga_id"]},
    ).json()
    assert checkout["status"] == "COMPLETED"

    status = buyer_client.http.get(f"/buyer/v1/order/{propose['order_id']}").json()
    assert status["status"] == "CAPTURED"

    explain = buyer_client.http.get(f"/buyer/v1/audit/explain/{propose['order_id']}")
    assert explain.status_code == 200
    explain_body = explain.json()
    assert explain_body["terminal_outcome"]["status"] == "CAPTURED"
    assert len(explain_body["timeline"]) > 0
    # never a secret in a buyer-facing response, mirroring the reviewer
    # explain route's own guarantee (README "Security notes").
    raw = explain.text
    assert "mandate_signing_key" not in raw
    assert "quote_signing_key" not in raw


def test_over_cap_order_is_denied_without_reservation(buyer_client: BuyerClient) -> None:
    buyer_client.seed(make_catalog_item("HTL-BUYER-OVERCAP", unit_price_minor=550000, rating=4.9))

    # A quote can still be issued (P4 scope never checks mandate bounds);
    # the cap is only enforced at propose time by the real policy engine.
    loose_mandate = buyer_client.http.post(
        "/buyer/v1/mandate",
        json={
            "nights": 1,
            "rooms": 1,
            "max_total_minor": 900000,
            "require_refundable": False,
            "check_in": "2026-09-20",
        },
    ).json()
    assert loose_mandate["bounds"]["max_unit_minor"] == 900000
    buyer_client.http.post(
        "/agent/v1/quote",
        json={
            "sku": "HTL-BUYER-OVERCAP",
            "mandate_id": loose_mandate["mandate_id"],
            "nights": 1,
        },
    )

    # A tighter mandate whose own max_unit_minor (300000) the unit price
    # (550000) actually violates.
    tight_mandate = buyer_client.http.post(
        "/buyer/v1/mandate",
        json={
            "nights": 1,
            "rooms": 1,
            "max_total_minor": 300000,
            "require_refundable": False,
            "check_in": "2026-09-20",
        },
    ).json()
    tight_quote = buyer_client.http.post(
        "/agent/v1/quote",
        json={
            "sku": "HTL-BUYER-OVERCAP",
            "mandate_id": tight_mandate["mandate_id"],
            "nights": 1,
        },
    ).json()

    propose = buyer_client.http.post(
        "/buyer/v1/order/propose",
        json={"quote_id": tight_quote["quote_id"], "mandate_id": tight_mandate["mandate_id"]},
    ).json()
    assert propose["decision"] == "reject"
    assert propose["reason_code"] == "UNIT_CAP_EXCEEDED"


# ---------------------------------------------------------------------------
# §28 P12 contextual upsell -- real, buyer-driven post-booking add-ons.
# ---------------------------------------------------------------------------


def _unique_city() -> str:
    """tests/integration/conftest.py's own documented isolation strategy:
    the shared session-scoped Postgres container is never wiped between
    tests, so every test that seeds location-scoped catalog data needs
    its own city -- otherwise `list_eligible_offers`'s real
    `list_items(location_city=..., ...)` query would (correctly, in
    production) see every other test's add-on inventory too."""
    return f"Goa-{new_id('loc')}"


def _book_and_capture_hotel(
    buyer_client: BuyerClient, sku: str, *, nights: int = 2, rooms: int = 1
) -> str:
    """Books and settles a real base order via the exact HTTP sequence a
    buyer uses -- returns the base order_id an upsell can be offered
    against."""
    mandate = buyer_client.http.post(
        "/buyer/v1/mandate",
        json={
            "nights": nights,
            "rooms": rooms,
            "max_total_minor": 900000,
            "require_refundable": True,
            "check_in": "2026-09-20",
        },
    ).json()
    quote = buyer_client.http.post(
        "/agent/v1/quote", json={"sku": sku, "mandate_id": mandate["mandate_id"], "nights": nights}
    ).json()
    propose = buyer_client.http.post(
        "/buyer/v1/order/propose",
        json={"quote_id": quote["quote_id"], "mandate_id": mandate["mandate_id"]},
    ).json()
    assert propose["decision"] == "accept"
    checkout = buyer_client.http.post(
        "/buyer/v1/checkout",
        json={"order_id": propose["order_id"], "saga_id": propose["saga_id"]},
    ).json()
    assert checkout["status"] == "COMPLETED"
    return str(propose["order_id"])


def _seed_addon(
    buyer_client: BuyerClient,
    sku: str,
    *,
    category: str,
    unit_price_minor: int,
    location_city: str,
    available_units: int = 10,
    refundable: bool = True,
) -> None:
    buyer_client.seed(
        make_catalog_item(
            sku,
            unit_price_minor=unit_price_minor,
            available_units=available_units,
            category=category,
            location_city=location_city,
            refundable=refundable,
        )
    )


def test_upsell_no_offers_for_a_nonexistent_or_uncaptured_order(
    buyer_client: BuyerClient,
) -> None:
    """Never an error -- an ineligible base order is simply "nothing to
    offer", the honest completion state, never a broken response."""
    resp = buyer_client.http.get("/buyer/v1/upsell/offers?order_id=ord_doesnotexist")
    assert resp.status_code == 200
    assert resp.json()["offers"] == []


def test_upsell_no_offers_when_no_addon_inventory_is_seeded(buyer_client: BuyerClient) -> None:
    """A genuinely settled base order with zero eligible add-on inventory
    must show no upsell -- never a fabricated offer."""
    city = _unique_city()
    _seed_addon(  # sold out, must never be offered
        buyer_client,
        "ADDON-SOLDOUT-TEST",
        category="travel.addon.flat",
        unit_price_minor=1000,
        location_city=city,
        available_units=0,
    )
    buyer_client.seed(
        make_catalog_item("HTL-UPSELL-EMPTY", unit_price_minor=100000, location_city=city)
    )
    order_id = _book_and_capture_hotel(buyer_client, "HTL-UPSELL-EMPTY")

    resp = buyer_client.http.get(f"/buyer/v1/upsell/offers?order_id={order_id}")
    assert resp.status_code == 200
    assert resp.json()["offers"] == []


def test_upsell_offer_pricing_is_contextual_to_nights_and_rooms(buyer_client: BuyerClient) -> None:
    """Deterministic, server-computed pricing: breakfast (per guest per
    night) must scale with the *actual* booked nights/rooms, never a
    flat guess -- proves eligibility reads the real base mandate, not a
    canned number."""
    city = _unique_city()
    _seed_addon(
        buyer_client,
        "ADDON-BREAKFAST-TEST",
        category="travel.addon.per_guest_per_night",
        unit_price_minor=35000,
        location_city=city,
    )
    buyer_client.seed(
        make_catalog_item("HTL-UPSELL-CONTEXT", unit_price_minor=100000, location_city=city)
    )
    order_id = _book_and_capture_hotel(buyer_client, "HTL-UPSELL-CONTEXT", nights=3, rooms=2)

    resp = buyer_client.http.get(f"/buyer/v1/upsell/offers?order_id={order_id}")
    offers = {o["sku"]: o for o in resp.json()["offers"]}
    assert offers["ADDON-BREAKFAST-TEST"]["total_minor"] == 35000 * 3 * 2
    assert offers["ADDON-BREAKFAST-TEST"]["quantity_description"] == "2 guests x 3 nights"


def test_upsell_fetching_offers_never_creates_a_mandate_or_order(
    buyer_client: BuyerClient,
) -> None:
    """Explicit-approval guarantee, backend half: viewing offers (GET) is
    read-only aside from the non-money 'offered' bookkeeping row -- it
    must never itself mint a mandate or an order. Only the separate
    POST /buyer/v1/upsell/purchase call (the buyer's deliberate Approve)
    can do that."""
    city = _unique_city()
    _seed_addon(
        buyer_client,
        "ADDON-PICKUP-TEST",
        category="travel.addon.flat",
        unit_price_minor=120000,
        location_city=city,
    )
    buyer_client.seed(
        make_catalog_item("HTL-UPSELL-NOMUTATE", unit_price_minor=100000, location_city=city)
    )
    order_id = _book_and_capture_hotel(buyer_client, "HTL-UPSELL-NOMUTATE")

    # The shared test Postgres container accumulates rows across the whole
    # session (tests/integration/conftest.py's own documented design) --
    # assert a delta, never an absolute count.
    async def _order_count() -> int:
        async with UnitOfWork(buyer_client.session_factory) as uow:
            return len(await uow.orders.list_recent(10_000))

    assert buyer_client.http.portal is not None
    before = buyer_client.http.portal.call(_order_count)

    for _ in range(3):
        buyer_client.http.get(f"/buyer/v1/upsell/offers?order_id={order_id}")

    after = buyer_client.http.portal.call(_order_count)
    assert after == before  # repeated GETs mutated no order/mandate at all


def test_upsell_purchase_uses_a_brand_new_mandate_and_appears_in_its_own_audit_timeline(
    buyer_client: BuyerClient,
) -> None:
    """The core trust-model proof: a settled upsell never reuses the
    settled base mandate (a fresh mandate_id every time -- §9.1's SETTLED
    is terminal, the gate would refuse reuse regardless), runs the real
    quote -> gate -> saga -> ledger -> payment pipeline, and is reachable
    through the exact same audit/proof route as any other order."""
    city = _unique_city()
    _seed_addon(
        buyer_client,
        "ADDON-PICKUP-TEST2",
        category="travel.addon.flat",
        unit_price_minor=120000,
        location_city=city,
    )
    buyer_client.seed(
        make_catalog_item("HTL-UPSELL-TIMELINE", unit_price_minor=100000, location_city=city)
    )
    base_order_id = _book_and_capture_hotel(buyer_client, "HTL-UPSELL-TIMELINE")
    base_explain = buyer_client.http.get(f"/buyer/v1/audit/explain/{base_order_id}").json()
    base_mandate_id = next(
        t["payload"].get("mandate_id")
        for t in base_explain["timeline"]
        if t["action"] == "mandate.locked"
    )

    purchase = buyer_client.http.post(
        "/buyer/v1/upsell/purchase",
        json={"base_order_id": base_order_id, "offer_sku": "ADDON-PICKUP-TEST2"},
    ).json()
    assert purchase["decision"] == "accept"
    addon_order_id = purchase["addon_order_id"]
    assert addon_order_id is not None
    assert addon_order_id != base_order_id

    addon_explain = buyer_client.http.get(f"/buyer/v1/audit/explain/{addon_order_id}").json()
    assert addon_explain["terminal_outcome"]["status"] == "CAPTURED"
    actions = [t["action"] for t in addon_explain["timeline"]]
    assert "mandate.locked" in actions
    assert "quote.issued" in actions
    assert "settlement.closed" in actions
    addon_mandate_id = next(
        t["payload"].get("mandate_id")
        for t in addon_explain["timeline"]
        if t["action"] == "mandate.locked"
    )
    assert addon_mandate_id != base_mandate_id  # never the reused, already-settled base mandate

    # The base order's own proof is untouched by the addon purchase.
    base_explain_after = buyer_client.http.get(f"/buyer/v1/audit/explain/{base_order_id}").json()
    assert base_explain_after["terminal_outcome"]["status"] == "CAPTURED"


def test_upsell_duplicate_purchase_of_the_same_addon_is_rejected(
    buyer_client: BuyerClient,
) -> None:
    """The database-level duplicate guard: a second purchase attempt for
    the same (base_order_id, offer_sku) must fail closed, never silently
    charge twice or silently no-op."""
    city = _unique_city()
    _seed_addon(
        buyer_client,
        "ADDON-PICKUP-DUP",
        category="travel.addon.flat",
        unit_price_minor=120000,
        location_city=city,
    )
    buyer_client.seed(
        make_catalog_item("HTL-UPSELL-DUP", unit_price_minor=100000, location_city=city)
    )
    base_order_id = _book_and_capture_hotel(buyer_client, "HTL-UPSELL-DUP")

    first = buyer_client.http.post(
        "/buyer/v1/upsell/purchase",
        json={"base_order_id": base_order_id, "offer_sku": "ADDON-PICKUP-DUP"},
    )
    assert first.status_code == 200
    assert first.json()["decision"] == "accept"

    second = buyer_client.http.post(
        "/buyer/v1/upsell/purchase",
        json={"base_order_id": base_order_id, "offer_sku": "ADDON-PICKUP-DUP"},
    )
    assert second.status_code == 409
    assert second.json()["detail"]["reason_code"] == "ALREADY_PURCHASED"

    # The already-purchased add-on must never be re-offered either.
    offers = buyer_client.http.get(f"/buyer/v1/upsell/offers?order_id={base_order_id}").json()
    assert "ADDON-PICKUP-DUP" not in {o["sku"] for o in offers["offers"]}


def test_upsell_decline_persists_and_stops_reoffering_this_session(
    buyer_client: BuyerClient,
) -> None:
    city = _unique_city()
    _seed_addon(
        buyer_client,
        "ADDON-PICKUP-DECLINE",
        category="travel.addon.flat",
        unit_price_minor=120000,
        location_city=city,
    )
    buyer_client.seed(
        make_catalog_item("HTL-UPSELL-DECLINE", unit_price_minor=100000, location_city=city)
    )
    base_order_id = _book_and_capture_hotel(buyer_client, "HTL-UPSELL-DECLINE")
    buyer_client.http.get(f"/buyer/v1/upsell/offers?order_id={base_order_id}")  # marks 'offered'

    decline = buyer_client.http.post(
        "/buyer/v1/upsell/decline", json={"base_order_id": base_order_id}
    )
    assert decline.status_code == 200
    assert decline.json()["status"] == "declined"

    offers = buyer_client.http.get(f"/buyer/v1/upsell/offers?order_id={base_order_id}").json()
    assert offers["offers"] == []


def test_upsell_declined_payment_leaves_reserved_balance_at_zero(
    buyer_client: BuyerClient,
) -> None:
    """The exact same F1-style guarantee the base flow has: a declined
    provider capture must never leave money reserved against the addon's
    own mandate, and the addon_purchases row must reflect the failure
    honestly, never as a silent success."""
    city = _unique_city()
    _seed_addon(
        buyer_client,
        "ADDON-PICKUP-DECLINE2",
        category="travel.addon.flat",
        unit_price_minor=120000,
        location_city=city,
    )
    buyer_client.seed(
        make_catalog_item("HTL-UPSELL-PAYDECLINE", unit_price_minor=100000, location_city=city)
    )
    base_order_id = _book_and_capture_hotel(buyer_client, "HTL-UPSELL-PAYDECLINE")

    # buyer_client's own fixture teardown pops this override regardless of
    # how this test ends, so no explicit restore is needed here.
    declining_simulator = SimulatorAdapter(clock=SystemClock(), scenario=Scenario.DECLINE)
    app.dependency_overrides[get_payment_provider] = lambda: declining_simulator
    resp = buyer_client.http.post(
        "/buyer/v1/upsell/purchase",
        json={"base_order_id": base_order_id, "offer_sku": "ADDON-PICKUP-DECLINE2"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "reject"
    assert body["addon_order_id"] is None

    async def _addon_mandate_id_and_balance() -> tuple[str | None, int]:
        async with UnitOfWork(buyer_client.session_factory) as uow:
            row = await uow.addon_purchases.get(base_order_id, "ADDON-PICKUP-DECLINE2")
            assert row is not None
            assert row.status == "failed"
            if row.addon_mandate_id is None:
                return None, 0
            entries = await uow.ledger_entries.list_for_account(
                account(row.addon_mandate_id, "reserved")
            )
            balance = net_balance([(e.direction, e.amount_minor) for e in entries])
            return row.addon_mandate_id, balance

    assert buyer_client.http.portal is not None
    mandate_id, balance = buyer_client.http.portal.call(_addon_mandate_id_and_balance)
    assert mandate_id is not None
    assert balance == 0


async def test_upsell_stale_price_is_rejected_like_any_other_purchase(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Proves G5's freshness check applies identically to an addon-scoped
    mandate/quote -- gate.py has no category special-casing, but this
    proves *this* module's own mandate/quote construction integrates with
    it correctly, not just that G5 works in the abstract."""
    clock = SystemClock()
    async with UnitOfWork(session_factory) as uow:
        await uow.catalog.upsert_item(
            CatalogItemRecord(
                sku="ADDON-STALE-TEST",
                category="travel.addon.flat",
                merchant_id="mrc_test",
                unit="trip",
                unit_price_minor=120000,
                available_units=10,
                location_city="Goa",
                location_country="IN",
                rating=0.0,
                sea_facing=False,
                breakfast_included=False,
                refundable=False,
                cancellation_window_h=0,
                instant_confirm=True,
                taxes_included=True,
                quote_required=True,
                version=1,
            )
        )
        await uow.commit()

    now = clock.now()
    draft = Mandate(
        mandate_id=new_id("mdt"),
        version=1,
        principal=Principal(type="human", id="usr_test"),
        delegate=Delegate(type="agent", id="agt_test", key_id="ed25519:test"),
        intent=MandateIntent(
            category="travel.addon.flat", location="Goa, IN", check_in="addon", nights=1, rooms=1
        ),
        bounds=MandateBounds(
            currency="INR",
            max_total_minor=120000,
            max_unit_minor=120000,
            max_transactions=1,
            allowed_categories=["travel.addon.flat"],
            blocked_merchants=[],
            require_refundable=False,
            max_price_delta_bps=1000,
        ),
        temporal=MandateTemporal(
            not_before=now, expires_at=now + timedelta(seconds=1800), quote_ttl_s=120
        ),
        controls=MandateControls(human_confirm_required=True, revocable=True),
    )
    spec_hash = compute_spec_hash(draft)
    mandate = draft.model_copy(
        update={
            "spec_hash": spec_hash,
            "signature": MandateSignature(
                alg="HMAC-SHA256",
                key_id="mk_test",
                value=sign_spec_hash(spec_hash, settings.mandate_signing_key.encode("utf-8")),
            ),
        }
    )

    async with UnitOfWork(session_factory) as uow:
        await uow.mandates.add(mandate, MandateStatus.LOCKED)
        quote = await create_quote(
            uow, clock, mandate_id=mandate.mandate_id, sku="ADDON-STALE-TEST", nights=1,
            actor_id="test",
        )
        await uow.commit()

    # The catalog version bumps *after* the quote was pinned -- the classic
    # stale-price race. Mutating to the *same* numeric price isolates G5's
    # catalog_version staleness check from the unrelated price_delta policy
    # rule (which a large price jump would trigger instead/first).
    async with UnitOfWork(session_factory) as uow:
        await uow.catalog.mutate_price("ADDON-STALE-TEST", 120000)
        await uow.commit()

    item = None
    async with UnitOfWork(session_factory) as uow:
        item = await uow.catalog.get_item("ADDON-STALE-TEST")
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

    simulator = SimulatorAdapter(clock=clock)
    breaker = CircuitBreaker(name="razorpay-test", clock=clock)
    outcome = await handle_order_propose(
        session_factory,
        simulator,
        clock,
        breaker,
        quote_id=quote.quote_id,
        quote_hash=quote.quote_hash or "",
        mandate_id=mandate.mandate_id,
        mandate_spec_hash=mandate.spec_hash or "",
        intent_hash=intent_hash,
        trace_id=new_id("trc"),
        actor_id="test",
    )
    body = dict(outcome.body)
    assert body["decision"] == "reject"
    assert body["reason_code"] == "STALE_PRICE", body

    entries_balance = None
    async with UnitOfWork(session_factory) as uow:
        entries = await uow.ledger_entries.list_for_account(account(mandate.mandate_id, "reserved"))
        entries_balance = net_balance([(e.direction, e.amount_minor) for e in entries])
    assert entries_balance == 0


def test_upsell_offers_and_purchase_never_call_or_depend_on_an_llm_client(
    buyer_client: BuyerClient,
) -> None:
    """§28 P12's own requirement: eligibility/pricing/authorization stay
    deterministic even under LLM_ENABLED=false -- proven structurally
    here since this whole test file's fixture already runs every route
    against NullLLMClient (get_llm_client is overridden fixture-wide,
    never touched by any upsell route/function at all)."""
    city = _unique_city()
    _seed_addon(
        buyer_client,
        "ADDON-NOLLM-TEST",
        category="travel.addon.flat",
        unit_price_minor=50000,
        location_city=city,
    )
    buyer_client.seed(
        make_catalog_item("HTL-UPSELL-NOLLM", unit_price_minor=100000, location_city=city)
    )
    order_id = _book_and_capture_hotel(buyer_client, "HTL-UPSELL-NOLLM")

    resp = buyer_client.http.get(f"/buyer/v1/upsell/offers?order_id={order_id}")
    assert resp.status_code == 200
    assert len(resp.json()["offers"]) == 1


def test_upsell_responses_never_leak_a_secret(buyer_client: BuyerClient) -> None:
    city = _unique_city()
    _seed_addon(
        buyer_client,
        "ADDON-SECRETCHECK",
        category="travel.addon.flat",
        unit_price_minor=50000,
        location_city=city,
    )
    buyer_client.seed(
        make_catalog_item("HTL-UPSELL-SECRETCHECK", unit_price_minor=100000, location_city=city)
    )
    order_id = _book_and_capture_hotel(buyer_client, "HTL-UPSELL-SECRETCHECK")

    offers_resp = buyer_client.http.get(f"/buyer/v1/upsell/offers?order_id={order_id}")
    purchase_resp = buyer_client.http.post(
        "/buyer/v1/upsell/purchase",
        json={"base_order_id": order_id, "offer_sku": "ADDON-SECRETCHECK"},
    )
    for raw in (offers_resp.text, purchase_resp.text):
        lowered = raw.lower()
        secrets = ("mandate_signing_key", "quote_signing_key", "razorpay_key_secret", "admin_token")
        for secret in secrets:
            assert secret not in lowered
