"""RazorpayAdapter (§15.1, §28 P5): real calls against Razorpay's
test-mode Orders and Payments APIs. Verified against Razorpay's own
current documentation during this phase, not assumed from memory — see
docs/adr/0006-p5-payments-decisions.md for the consulted URLs.

Endpoints (HTTP Basic Auth: key_id:key_secret, base64-encoded):
  POST /v1/orders                     -- create an order
  GET  /v1/payments/{id}               -- fetch one payment
  GET  /v1/orders/{id}/payments        -- fetch all payments for an order
  POST /v1/payments/{id}/capture       -- capture an authorized payment

Idempotency: this system's derived key (§15.2) is sent as the order's
`receipt` field — Razorpay's own documented unique-per-account identifier
(max 40 ASCII chars; the derived key is 35) — the create-order endpoint
has no separate request-level idempotency-key field.

Never logs: the Authorization header, key_id/key_secret/webhook_secret, a
raw checkout/webhook signature, or full raw card/payment details — only
Razorpay's own {code, description} error shape, which contains neither.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from typing import Any

import httpx2 as httpx

from actl.application.ports import (
    ProviderOrder,
    ProviderPayment,
    ProviderRefund,
    TerminalProviderError,
    TransientProviderError,
)
from actl.domain.mandate.signing import verify_signature
from actl.platform.logging import get_logger

logger = get_logger(__name__)

BASE_URL = "https://api.razorpay.com/v1"


class RazorpayAdapter:
    def __init__(
        self, *, key_id: str, key_secret: str, webhook_secret: str, timeout_s: float
    ) -> None:
        if not key_id.startswith("rzp_test_"):
            # §21.4 / P0: config.py's startup check already refuses to
            # boot the whole app on a live key; this is a redundant,
            # adapter-local guard for anyone constructing RazorpayAdapter
            # directly (e.g. a standalone script) rather than through
            # settings. Fails closed, loudly, same message shape as P0's.
            raise SystemExit(
                "FATAL: RazorpayAdapter refuses to run against a non-test-mode key "
                f"(prefix {key_id[:9]!r}). This build has no authorisation to move real money."
            )
        self._key_secret = key_secret.encode("utf-8")
        self._webhook_secret = webhook_secret.encode("utf-8")
        self._client = httpx.AsyncClient(
            base_url=BASE_URL, auth=(key_id, key_secret), timeout=timeout_s
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def create_order(
        self, amount_minor: int, currency: str, idempotency_key: str, notes: dict[str, str]
    ) -> ProviderOrder:
        body = {
            "amount": amount_minor,
            "currency": currency,
            "receipt": idempotency_key,
            "notes": notes,
        }
        data = await self._request("POST", "/orders", json_body=body)
        return _to_provider_order(data)

    async def fetch_payments(self, provider_order_id: str) -> list[ProviderPayment]:
        data = await self._request("GET", f"/orders/{provider_order_id}/payments")
        items = data.get("items", [])
        return [_to_provider_payment(item) for item in items]

    async def capture(self, payment_id: str, amount_minor: int) -> ProviderPayment:
        body = {"amount": amount_minor, "currency": "INR"}
        data = await self._request("POST", f"/payments/{payment_id}/capture", json_body=body)
        return _to_provider_payment(data)

    async def refund(
        self, payment_id: str, amount_minor: int, idempotency_key: str
    ) -> ProviderRefund:
        body = {"amount": amount_minor, "receipt": idempotency_key}
        data = await self._request("POST", f"/payments/{payment_id}/refund", json_body=body)
        status = data.get("status")
        return ProviderRefund(
            id=data["id"],
            payment_id=payment_id,
            amount_minor=data["amount"],
            status=status if isinstance(status, str) else "processed",
        )

    def verify_checkout_signature(self, order_id: str, payment_id: str, signature: str) -> bool:
        """§15.4: hmac_sha256(order_id + "|" + payment_id, key_secret),
        constant-time compared. Reuses P1's exact signing primitive
        (actl.domain.mandate.signing) rather than a parallel HMAC call."""
        if not order_id or not payment_id or not signature:
            return False
        return verify_signature(f"{order_id}|{payment_id}", self._key_secret, signature)

    def verify_webhook(self, raw_body: bytes, signature: str) -> bool:
        """§15.3: HMAC-SHA256 of the raw body with the webhook secret,
        constant-time compared."""
        if not signature:
            return False
        expected = hmac.new(self._webhook_secret, raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    async def _request(
        self, method: str, path: str, *, json_body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(method, path, json=json_body)
        except httpx.TimeoutException as exc:
            logger.warning("razorpay.request_timeout", method=method, path=path)
            raise TransientProviderError(f"razorpay request timed out: {method} {path}") from exc
        except httpx.TransportError as exc:
            logger.warning("razorpay.transport_error", method=method, path=path)
            raise TransientProviderError(f"razorpay transport error: {method} {path}") from exc

        if response.status_code >= 500:
            logger.warning(
                "razorpay.server_error", method=method, path=path, status=response.status_code
            )
            raise TransientProviderError(
                f"razorpay server error {response.status_code}: {method} {path}"
            )
        if response.status_code >= 400:
            error = _safe_error_body(response)
            logger.warning(
                "razorpay.client_error",
                method=method,
                path=path,
                status=response.status_code,
                error_code=error.get("code"),
            )
            raise TerminalProviderError(
                f"razorpay rejected the request ({response.status_code}): {method} {path}: "
                f"{error.get('code')}"
            )

        result: dict[str, Any] = response.json()
        return result


def _safe_error_body(response: httpx.Response) -> dict[str, Any]:
    """Razorpay error responses are `{"error": {"code", "description", ...}}`
    — code/description are safe to log (no secrets, no card data)."""
    try:
        body = response.json()
    except ValueError:
        return {}
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return {}
    return {"code": error.get("code"), "description": error.get("description")}


def _to_provider_order(data: dict[str, Any]) -> ProviderOrder:
    return ProviderOrder(
        id=data["id"],
        status=data["status"],
        amount_minor=data["amount"],
        currency=data["currency"],
        receipt=data.get("receipt"),
        created_at=datetime.fromtimestamp(data["created_at"], tz=UTC),
    )


def _to_provider_payment(data: dict[str, Any]) -> ProviderPayment:
    return ProviderPayment(
        id=data["id"],
        order_id=data.get("order_id"),
        status=data["status"],
        amount_minor=data["amount"],
        currency=data["currency"],
        captured=bool(data.get("captured", False)),
        method=data.get("method"),
        error_code=data.get("error_code"),
        error_description=data.get("error_description"),
    )
