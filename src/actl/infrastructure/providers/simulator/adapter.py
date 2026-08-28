"""Deterministic, zero-network PaymentProvider (§15.1, §28 P5): every
automated test and injected failure scenario uses this, never a real
Razorpay call. Produces the same entity shapes RazorpayAdapter does,
including realistic failure codes, so application code cannot tell them
apart except by which one was injected.

Checkout and webhook signatures are *real* HMAC-SHA256 (via the same
`sign_spec_hash`/`verify_signature` primitives P1's mandate module built —
§28 P4 ADR precedent), never faked. A "tampered signature" scenario is a
test mutating a real, valid signature, exactly like scripts/tamper.py
mutates a real audit row — never a flag that makes the adapter lie about
its own cryptography.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from enum import StrEnum

from actl.application.ports import (
    ProviderOrder,
    ProviderPayment,
    ProviderRefund,
    TerminalProviderError,
    TransientProviderError,
)
from actl.domain.mandate.signing import sign_spec_hash, verify_signature
from actl.platform.clock import Clock
from actl.platform.ids import new_id


class Scenario(StrEnum):
    SUCCESS = "success"
    DECLINE = "decline"
    TRANSIENT_FAILURE = "transient_failure"


_DECLINE_ERROR_CODE = "BAD_REQUEST_ERROR"
_DECLINE_ERROR_DESCRIPTION = "Payment failed: card declined by issuing bank (simulated)"


@dataclass
class SimulatorAdapter:
    clock: Clock
    scenario: Scenario = Scenario.SUCCESS
    key_secret: bytes = b"simulator-checkout-secret"
    webhook_secret: bytes = b"simulator-webhook-secret"
    fail_before_success: int = 0  # TRANSIENT_FAILURE: succeed on call number (n+1)

    _orders: dict[str, ProviderOrder] = field(default_factory=dict, init=False)
    _payments: dict[str, list[ProviderPayment]] = field(default_factory=dict, init=False)
    _call_count: int = field(default=0, init=False)

    async def create_order(
        self, amount_minor: int, currency: str, idempotency_key: str, notes: dict[str, str]
    ) -> ProviderOrder:
        self._call_count += 1
        if (
            self.scenario is Scenario.TRANSIENT_FAILURE
            and self._call_count <= self.fail_before_success
        ):
            raise TransientProviderError("simulated provider timeout on order creation")

        order_id = new_id("order")
        order = ProviderOrder(
            id=order_id,
            status="created",
            amount_minor=amount_minor,
            currency=currency,
            receipt=idempotency_key,
            created_at=self.clock.now(),
        )
        self._orders[order_id] = order

        payment_id = new_id("pay")
        if self.scenario is Scenario.DECLINE:
            payment = ProviderPayment(
                id=payment_id,
                order_id=order_id,
                status="failed",
                amount_minor=amount_minor,
                currency=currency,
                captured=False,
                method="card",
                error_code=_DECLINE_ERROR_CODE,
                error_description=_DECLINE_ERROR_DESCRIPTION,
            )
        else:
            payment = ProviderPayment(
                id=payment_id,
                order_id=order_id,
                status="authorized",
                amount_minor=amount_minor,
                currency=currency,
                captured=False,
                method="card",
            )
        self._payments[order_id] = [payment]
        return order

    async def fetch_payments(self, provider_order_id: str) -> list[ProviderPayment]:
        return list(self._payments.get(provider_order_id, []))

    async def capture(self, payment_id: str, amount_minor: int) -> ProviderPayment:
        for order_id, payments in self._payments.items():
            for i, p in enumerate(payments):
                if p.id != payment_id:
                    continue
                if p.status == "failed":
                    raise TerminalProviderError(f"cannot capture a failed payment {payment_id}")
                captured = ProviderPayment(
                    id=p.id,
                    order_id=p.order_id,
                    status="captured",
                    amount_minor=amount_minor,
                    currency=p.currency,
                    captured=True,
                    method=p.method,
                )
                self._payments[order_id][i] = captured
                return captured
        raise TerminalProviderError(f"no such payment {payment_id}")

    async def refund(
        self, payment_id: str, amount_minor: int, idempotency_key: str
    ) -> ProviderRefund:
        return ProviderRefund(
            id=new_id("rfnd"), payment_id=payment_id, amount_minor=amount_minor, status="processed"
        )

    def verify_checkout_signature(self, order_id: str, payment_id: str, signature: str) -> bool:
        if not order_id or not payment_id or not signature:
            return False
        return verify_signature(f"{order_id}|{payment_id}", self.key_secret, signature)

    def verify_webhook(self, raw_body: bytes, signature: str) -> bool:
        if not signature:
            return False
        expected = hmac.new(self.webhook_secret, raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    # ---- test helpers, not part of the PaymentProvider protocol -----------

    def build_checkout_payload(self, order_id: str, payment_id: str) -> str:
        """A real, valid signature over (order_id, payment_id) using this
        adapter's key_secret. A test wanting a tampered signature mutates
        the returned string itself."""
        return sign_spec_hash(f"{order_id}|{payment_id}", self.key_secret)

    def build_webhook_payload(
        self,
        event_type: str,
        *,
        provider_order_id: str,
        provider_payment_id: str,
        amount_minor: int,
        currency: str = "INR",
    ) -> tuple[bytes, str, str]:
        """Returns (raw_body, signature, event_id) for a realistic
        payment.captured/payment.failed-shaped delivery, signed with this
        adapter's webhook_secret. event_id mirrors Razorpay's
        X-Razorpay-Event-Id header (unique per *delivery*) — reuse the same
        returned event_id across two calls to simulate a duplicate
        delivery of one logical event."""
        event_id = new_id("evt")
        body = {
            "entity": "event",
            "account_id": "acc_simulator",
            "event": event_type,
            "contains": ["payment"],
            "payload": {
                "payment": {
                    "entity": {
                        "id": provider_payment_id,
                        "entity": "payment",
                        "order_id": provider_order_id,
                        "amount": amount_minor,
                        "currency": currency,
                        "status": "captured" if event_type == "payment.captured" else "failed",
                        "captured": event_type == "payment.captured",
                    }
                }
            },
            "created_at": int(self.clock.now().timestamp()),
        }
        raw_body = json.dumps(body).encode("utf-8")
        signature = hmac.new(self.webhook_secret, raw_body, hashlib.sha256).hexdigest()
        return raw_body, signature, event_id
