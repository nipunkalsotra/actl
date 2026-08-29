"""Ports (protocols) the application layer depends on; infrastructure
supplies the concrete adapters. Accumulates over phases (§25) — P3 added
`Anchor`; P5 adds `PaymentProvider`. `LLMClient`, `EventBus` land with
whichever later phase first needs to inject or mock one (same reasoning as
ADR 0003 decision 8 for `UnitOfWork` not getting a port until P6)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from actl.platform.errors import ExternalServiceError


class Anchor(Protocol):
    """§16.1: optional external timestamping for a Merkle checkpoint root —
    "the root, and only the root," never business data. A no-op default
    (infrastructure/anchor/noop.py) means the stretch goal of real-chain
    anchoring can never block the critical path (§28 P3 Key decision)."""

    async def anchor_root(self, merkle_root: str) -> str | None:
        """Publish `merkle_root` externally, returning a reference (e.g. a
        transaction id) once anchored, or None if this adapter doesn't
        anchor at all. None is a normal, expected result — not a failure."""
        ...


# ---------------------------------------------------------------------------
# §15.1 PaymentProvider port
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderOrder:
    id: str
    status: str
    amount_minor: int
    currency: str
    receipt: str | None
    created_at: datetime


@dataclass(frozen=True)
class ProviderPayment:
    id: str
    order_id: str | None
    status: str
    amount_minor: int
    currency: str
    captured: bool
    method: str | None
    error_code: str | None = None
    error_description: str | None = None


@dataclass(frozen=True)
class ProviderRefund:
    id: str
    payment_id: str
    amount_minor: int
    status: str


class TransientProviderError(ExternalServiceError):
    """§20 F5: a timeout, network failure, or 5xx from the provider — safe
    to retry with the same idempotency key. Never raised for a decline."""

    reason_code = "PROVIDER_TRANSIENT"


class TerminalProviderError(ExternalServiceError):
    """§20 F2: a payment decline or a provider-confirmed permanent failure
    (bad request, auth rejected the payment, already-failed payment). Must
    never be retried — the caller compensates instead."""

    reason_code = "PROVIDER_TERMINAL"


class PaymentProvider(Protocol):
    """§15.1. Two implementations: RazorpayAdapter (infrastructure) makes
    real calls against Razorpay's test-mode APIs; SimulatorAdapter
    (infrastructure) is deterministic and network-free. Application code
    (application/payment_service.py) depends only on this Protocol —
    never on either concrete adapter — mirroring the Anchor port's
    dependency-inversion shape (ADR 0004 decision 9)."""

    async def create_order(
        self, amount_minor: int, currency: str, idempotency_key: str, notes: dict[str, str]
    ) -> ProviderOrder: ...

    async def fetch_payments(self, provider_order_id: str) -> list[ProviderPayment]: ...

    async def capture(self, payment_id: str, amount_minor: int) -> ProviderPayment: ...

    async def refund(
        self, payment_id: str, amount_minor: int, idempotency_key: str
    ) -> ProviderRefund: ...

    def verify_checkout_signature(self, order_id: str, payment_id: str, signature: str) -> bool:
        """The payer's authorization, §15.4 — HMAC-SHA256 of
        `order_id + "|" + payment_id` with the key secret, constant-time
        compared. This, not the mandate, is what makes `capture()`
        reachable."""
        ...

    def verify_webhook(self, raw_body: bytes, signature: str) -> bool:
        """The provider's async notice, §15.3 — HMAC-SHA256 of the raw
        body with the webhook secret, constant-time compared. Evidence,
        never sole truth."""
        ...


# ---------------------------------------------------------------------------
# §17 LLMClient port
# ---------------------------------------------------------------------------


class LLMUnavailable(ExternalServiceError):
    """§17.2/§17.3: raised for *every* failure mode a caller must react to
    by falling back to deterministic code -- timeout, network error, rate
    limited, circuit open, LLM_ENABLED=false, or a response that still
    isn't valid JSON after the schema-repair loop. Callers never
    distinguish sub-reasons; the contract is identical either way. Never
    raised for a syntactically-valid JSON object that fails the caller's
    own Pydantic schema -- that is the caller's referential-validation
    job, using the value this port did successfully return."""

    reason_code = "LLM_UNAVAILABLE"


class LLMClient(Protocol):
    """§17. Three bounded uses (U1 extraction, U2 ranking, U3 narration)
    share this one port -- two shapes cover all of them: JSON mode for the
    two that must produce a schema-valid structure, plain text for
    narration prose. Temperature 0 and the timeout/breaker/rate-limit/
    cache machinery are all internal to the concrete adapter
    (`infrastructure/llm/`); application code never sees any of that,
    exactly mirroring how `PaymentProvider` hides Razorpay's own retry and
    auth concerns from `application/gate.py`."""

    async def complete_json(
        self, *, system: str, user: str, max_tokens: int
    ) -> dict[str, object]:
        """Temperature 0, JSON object mode. Raises `LLMUnavailable` on any
        failure. On success, returns *some* parsed JSON object -- schema
        validity against a specific use case's contract is always the
        caller's job."""
        ...

    async def complete_text(self, *, system: str, user: str, max_tokens: int) -> str:
        """Temperature 0, plain text. Raises `LLMUnavailable` on failure."""
        ...
