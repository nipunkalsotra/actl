"""Picks RazorpayAdapter vs SimulatorAdapter from settings (§28 P5). The
only place either concrete adapter is imported outside `infrastructure`
and outside a test: called from `actl.main`/`actl.cli`/`actl.worker`, none
of which are inside the `actl.interfaces`/`actl.application` packages the
import-linter "Only the gate may reach a payment provider" contract
constrains -- application code never imports this module, it receives the
constructed `PaymentProvider` as a parameter instead (§28 P5 instruction 1;
see docs/adr/0006-p5-payments-decisions.md).
"""

from __future__ import annotations

from actl.application.ports import PaymentProvider
from actl.config import Settings
from actl.infrastructure.providers.razorpay.adapter import RazorpayAdapter
from actl.infrastructure.providers.simulator.adapter import SimulatorAdapter
from actl.platform.clock import SystemClock


def build_payment_provider(settings: Settings) -> PaymentProvider:
    if settings.payment_provider == "razorpay":
        return RazorpayAdapter(
            key_id=settings.razorpay_key_id,
            key_secret=settings.razorpay_key_secret,
            webhook_secret=settings.razorpay_webhook_secret,
            timeout_s=settings.provider_timeout_s,
        )
    if settings.payment_provider == "simulator":
        return SimulatorAdapter(clock=SystemClock())
    raise ValueError(f"unknown payment_provider: {settings.payment_provider!r}")
