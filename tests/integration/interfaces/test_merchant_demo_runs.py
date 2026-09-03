"""§28 Trust Lab: `POST /merchant/v1/demo-runs` + `GET /merchant/v1/demo-runs/
{run_id}` -- the live, pollable run surface `application.demo_runs` adds
alongside the four original synchronous `/merchant/v1/demo/*` routes
(still covered, unchanged, by `test_merchant_router.py`).

Same TestClient-background-loop precedent as the rest of this directory
(ADR 0005 decision 12): the background task `start_demo_run` schedules
runs on that same background thread's event loop, so it keeps making
progress between polls issued from this (synchronous) test.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from tests.integration.interfaces.test_merchant_router import MerchantClient
from tests.integration.interfaces.test_merchant_router import merchant_client as merchant_client

from actl.config import settings


def _poll_until_terminal(
    client: MerchantClient, run_id: str, *, timeout_s: float = 10.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        resp = client.http.get(f"/merchant/v1/demo-runs/{run_id}")
        assert resp.status_code == 200
        body = resp.json()
        if body["status"] in ("passed", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not reach a terminal state within {timeout_s}s")


def _start_and_wait(client: MerchantClient, scenario: str) -> dict[str, Any]:
    start = client.http.post("/merchant/v1/demo-runs", json={"scenario": scenario})
    assert start.status_code == 200
    run_id = start.json()["run_id"]
    return _poll_until_terminal(client, run_id)


def test_start_demo_run_rejects_when_not_simulator(
    merchant_client: MerchantClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "payment_provider", "razorpay")
    resp = merchant_client.http.post("/merchant/v1/demo-runs", json={"scenario": "stale_price"})
    assert resp.status_code == 403


def test_get_demo_run_rejects_persistent_demo_app_env(
    merchant_client: MerchantClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "app_env", "demo")
    resp = merchant_client.http.get("/merchant/v1/demo-runs/run_doesnotmatter")
    assert resp.status_code == 403


def test_unknown_run_id_404s(merchant_client: MerchantClient) -> None:
    resp = merchant_client.http.get("/merchant/v1/demo-runs/run_totally_unknown")
    assert resp.status_code == 404
    assert resp.json()["detail"]["reason_code"] == "RUN_NOT_FOUND"


def test_unknown_scenario_404s(merchant_client: MerchantClient) -> None:
    resp = merchant_client.http.post("/merchant/v1/demo-runs", json={"scenario": "not_a_scenario"})
    assert resp.status_code == 404


def _assert_events_strictly_ordered(events: list[dict[str, Any]]) -> None:
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    timestamps = [e["ts"] for e in events]
    assert timestamps == sorted(timestamps), "events must arrive in non-decreasing ts order"


def test_stale_price_run_detects_g5_then_recovers_and_captures(
    merchant_client: MerchantClient,
) -> None:
    run = _start_and_wait(merchant_client, "stale_price")
    assert run["status"] == "passed"
    assert run["error"] is None

    events = run["events"]
    assert len(events) >= 8
    _assert_events_strictly_ordered(events)

    kinds = [e["kind"] for e in events]
    assert "catalog.price_mutated" in kinds, "the out-of-band mutation must itself be audited"
    assert "order.proposed.denied" in kinds, "the first, stale attempt must actually be blocked"
    assert "order.proposed.allowed" in kinds, "the recovered, re-quoted attempt must succeed"

    denied = next(e for e in events if e["kind"] == "order.proposed.denied")
    assert denied["status"] == "blocked"
    assert denied["evidence"]["gate"] == "G5"
    assert denied["evidence"]["reason_code"] == "STALE_PRICE"
    # the denial must land strictly before the successful recovery attempt
    allowed = next(e for e in events if e["kind"] == "order.proposed.allowed")
    assert denied["seq"] < allowed["seq"]

    mutated = next(e for e in events if e["kind"] == "catalog.price_mutated")
    assert mutated["seq"] < denied["seq"], "the fault injection must precede its own detection"

    result = run["result"]
    assert result["scenario"] == "stale_price"
    assert result["detected_fault"].startswith("STALE_PRICE")
    assert result["terminal_outcome"] == "CAPTURED"
    assert result["reserved_balance_minor"] == 0, "settled funds move out of the reserved bucket"
    assert result["order_id"] is not None
    assert result["chain_verified"] is True


def test_declined_run_shows_compensation_and_zero_reservation(
    merchant_client: MerchantClient,
) -> None:
    run = _start_and_wait(merchant_client, "declined")
    assert run["status"] == "passed"

    events = run["events"]
    _assert_events_strictly_ordered(events)
    kinds = [e["kind"] for e in events]
    assert "compensation.applied" in kinds

    compensation = next(e for e in events if e["kind"] == "compensation.applied")
    assert compensation["status"] == "compensated"
    assert compensation["evidence"]["reason_code"] == "PROVIDER_DECLINED"
    assert compensation["evidence"]["reserved_balance_minor"] == 0
    assert compensation["evidence"]["released_balance_minor"] > 0

    result = run["result"]
    assert result["detected_fault"] == "PROVIDER_DECLINED"
    assert "COMPENSATED" in result["terminal_outcome"]
    assert result["reserved_balance_minor"] == 0


def test_llm_down_run_shows_deterministic_fallback_before_any_money_step(
    merchant_client: MerchantClient,
) -> None:
    run = _start_and_wait(merchant_client, "llm_down")
    assert run["status"] == "passed"

    events = run["events"]
    _assert_events_strictly_ordered(events)
    kinds = [e["kind"] for e in events]
    assert "llm.extraction_fallback" in kinds
    assert "llm.ranking_fallback" in kinds

    extraction = next(e for e in events if e["kind"] == "llm.extraction_fallback")
    ranking = next(e for e in events if e["kind"] == "llm.ranking_fallback")
    first_money_event = next(
        (e for e in events if e["phase"] in ("quote", "gate", "ledger", "payment", "settlement")),
        None,
    )
    assert first_money_event is not None
    assert extraction["seq"] < first_money_event["seq"]
    assert ranking["seq"] < first_money_event["seq"]
    assert extraction["status"] == "passed"
    assert ranking["status"] == "passed"

    result = run["result"]
    assert result["detected_fault"] == "LLM_UNAVAILABLE (every U1/U2 call)"
    assert result["terminal_outcome"] == "CAPTURED"


def test_verify_chain_run_never_falsely_claims_anchoring(merchant_client: MerchantClient) -> None:
    run = _start_and_wait(merchant_client, "verify_chain")
    assert run["status"] == "passed"

    events = run["events"]
    _assert_events_strictly_ordered(events)
    kinds = [e["kind"] for e in events]
    assert "chain.entries_verified" in kinds
    assert "chain.terminal" in kinds

    # ANCHOR_PROVIDER=noop by default in this test environment -- no
    # checkpoint this run reports on may claim "anchored".
    for e in events:
        if e["kind"] == "checkpoint.merkle_matched":
            assert e["evidence"]["checkpoint_status"] != "anchored"

    result = run["result"]
    assert result["terminal_outcome"] in ("CHAIN VALID", "CHAIN EMPTY") or result[
        "terminal_outcome"
    ].startswith("CHAIN BROKEN")


@pytest.mark.parametrize("scenario", ["stale_price", "declined", "llm_down", "verify_chain"])
def test_events_never_leak_secrets(merchant_client: MerchantClient, scenario: str) -> None:
    run = _start_and_wait(merchant_client, scenario)
    dumped = str(run)
    secrets = [
        settings.mandate_signing_key,
        settings.quote_signing_key,
        settings.admin_token,
    ]
    for secret in secrets:
        if secret:
            assert secret not in dumped


def test_demo_orders_do_not_affect_organic_kpi_totals(merchant_client: MerchantClient) -> None:
    before = merchant_client.http.get("/merchant/v1/kpis").json()
    _start_and_wait(merchant_client, "declined")
    after = merchant_client.http.get("/merchant/v1/kpis").json()

    assert after["organic"]["orders"] == before["organic"]["orders"]
    assert after["organic"]["gross_sales_minor"] == before["organic"]["gross_sales_minor"]
