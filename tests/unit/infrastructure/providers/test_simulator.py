from __future__ import annotations

from datetime import UTC, datetime

import pytest

from actl.application.ports import TerminalProviderError, TransientProviderError
from actl.infrastructure.providers.simulator.adapter import Scenario, SimulatorAdapter
from actl.platform.clock import FrozenClock

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _clock() -> FrozenClock:
    return FrozenClock(at=NOW)


async def test_success_scenario_creates_order_and_authorized_payment() -> None:
    provider = SimulatorAdapter(clock=_clock(), scenario=Scenario.SUCCESS)
    order = await provider.create_order(100000, "INR", "ik_test1", notes={})
    assert order.status == "created"
    assert order.amount_minor == 100000
    assert order.receipt == "ik_test1"

    payments = await provider.fetch_payments(order.id)
    assert len(payments) == 1
    assert payments[0].status == "authorized"
    assert payments[0].error_code is None


async def test_decline_scenario_produces_failed_payment() -> None:
    provider = SimulatorAdapter(clock=_clock(), scenario=Scenario.DECLINE)
    order = await provider.create_order(100000, "INR", "ik_test2", notes={})
    payments = await provider.fetch_payments(order.id)
    assert payments[0].status == "failed"
    assert payments[0].error_code is not None


async def test_decline_scenario_capture_raises_terminal() -> None:
    provider = SimulatorAdapter(clock=_clock(), scenario=Scenario.DECLINE)
    order = await provider.create_order(100000, "INR", "ik_test3", notes={})
    payments = await provider.fetch_payments(order.id)
    with pytest.raises(TerminalProviderError):
        await provider.capture(payments[0].id, 100000)


async def test_transient_failure_raises_until_fail_before_success_exhausted() -> None:
    provider = SimulatorAdapter(
        clock=_clock(), scenario=Scenario.TRANSIENT_FAILURE, fail_before_success=2
    )
    with pytest.raises(TransientProviderError):
        await provider.create_order(100000, "INR", "ik_test4a", notes={})
    with pytest.raises(TransientProviderError):
        await provider.create_order(100000, "INR", "ik_test4b", notes={})
    order = await provider.create_order(100000, "INR", "ik_test4c", notes={})
    assert order.status == "created"


async def test_capture_moves_payment_to_captured() -> None:
    provider = SimulatorAdapter(clock=_clock())
    order = await provider.create_order(100000, "INR", "ik_test5", notes={})
    payments = await provider.fetch_payments(order.id)
    captured = await provider.capture(payments[0].id, 100000)
    assert captured.status == "captured"
    assert captured.captured is True

    refreshed = await provider.fetch_payments(order.id)
    assert refreshed[0].status == "captured"


async def test_capture_unknown_payment_raises_terminal() -> None:
    provider = SimulatorAdapter(clock=_clock())
    with pytest.raises(TerminalProviderError):
        await provider.capture("pay_does_not_exist", 100000)


def test_checkout_signature_round_trip_verifies() -> None:
    provider = SimulatorAdapter(clock=_clock())
    signature = provider.build_checkout_payload("order_x", "pay_y")
    assert provider.verify_checkout_signature("order_x", "pay_y", signature) is True


def test_checkout_signature_rejects_tampered_signature() -> None:
    provider = SimulatorAdapter(clock=_clock())
    signature = provider.build_checkout_payload("order_x", "pay_y")
    tampered = signature[:-1] + ("0" if signature[-1] != "0" else "1")
    assert provider.verify_checkout_signature("order_x", "pay_y", tampered) is False


def test_checkout_signature_rejects_missing_values() -> None:
    provider = SimulatorAdapter(clock=_clock())
    assert provider.verify_checkout_signature("", "pay_y", "sig") is False
    assert provider.verify_checkout_signature("order_x", "", "sig") is False
    assert provider.verify_checkout_signature("order_x", "pay_y", "") is False


def test_webhook_signature_round_trip_verifies() -> None:
    provider = SimulatorAdapter(clock=_clock())
    raw_body, signature, event_id = provider.build_webhook_payload(
        "payment.captured",
        provider_order_id="order_x",
        provider_payment_id="pay_y",
        amount_minor=1000,
    )
    assert provider.verify_webhook(raw_body, signature) is True
    assert event_id.startswith("evt_")


def test_webhook_signature_rejects_tampered_body() -> None:
    provider = SimulatorAdapter(clock=_clock())
    raw_body, signature, _ = provider.build_webhook_payload(
        "payment.captured",
        provider_order_id="order_x",
        provider_payment_id="pay_y",
        amount_minor=1000,
    )
    tampered_body = raw_body + b" "
    assert provider.verify_webhook(tampered_body, signature) is False


def test_webhook_signature_rejects_missing_signature() -> None:
    provider = SimulatorAdapter(clock=_clock())
    assert provider.verify_webhook(b"{}", "") is False
