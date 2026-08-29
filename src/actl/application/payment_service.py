"""§15: payment-provider orchestration -- order creation, checkout-signature
gated capture, webhook receipt/processing, and reconciliation. Depends only
on the `PaymentProvider` port (application.ports); never imports a
concrete adapter (§28 P5 instruction 1; see
docs/adr/0006-p5-payments-decisions.md decision 1).

`create_provider_order` takes a `session_factory`, not a pre-opened
`UnitOfWork`, because it needs *two* separate, sequential transactions:
the idempotency claim + local order row + `payment.intent` audit entry
must commit and become durable *before* the external provider call
(§7 step 13's "written before the call", §11.2's G7) -- holding a DB
transaction open across a slow network call would be its own bug. Every
other function here takes one `uow` and does one transaction, matching
the rest of the codebase.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal, cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application.audit_service import append_entry
from actl.application.integrity import raise_if_halted
from actl.application.ports import (
    PaymentProvider,
    ProviderPayment,
    TerminalProviderError,
    TransientProviderError,
)
from actl.config import settings
from actl.domain.audit.events import AuditAction
from actl.infrastructure.db.repositories.idempotency_keys import IdempotencyKeyRecord
from actl.infrastructure.db.repositories.orders import (
    TERMINAL_STATUSES,
    OrderRecord,
)
from actl.infrastructure.db.repositories.webhook_events import WebhookEventRecord
from actl.infrastructure.db.uow import UnitOfWork
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import Clock
from actl.platform.errors import ActlError, CircuitOpenError
from actl.platform.ids import new_id
from actl.platform.retry import RetryExhausted, retry_with_full_jitter

_SleepFn = Callable[[float], Awaitable[None]]


class MandateNotFound(ActlError):
    reason_code = "MANDATE_NOT_FOUND"


class CheckoutSignatureInvalid(ActlError):
    """§15.4: a signature that fails verification. `capture()` is never
    called when this is raised -- it is raised *instead of* calling it."""

    reason_code = "PROVIDER_DECLINED"


class IdempotencyInFlightTimeout(ActlError):
    """Another attempt claimed this key and has not completed within the
    bounded wait. Rare in practice (the provider call is what takes time,
    and this system's own retry/circuit-breaker bounds that); the caller
    should retry the whole request later, never bypass the claim."""

    reason_code = "IDEMPOTENCY_IN_FLIGHT"


class IdempotentAttemptFailed(ActlError):
    """A replay of a key whose original attempt ended in terminal failure.
    §15.2: "a genuinely NEW attempt after a terminal failure increments
    attempt_no, producing a new key" -- this is not that; it is a pure
    replay, so it returns the same failure, never a fresh provider call."""

    reason_code = "PROVIDER_TERMINAL"


def compute_idempotency_key(mandate_id: str, intent_hash: str, attempt_no: int) -> str:
    """§15.2 exact formula."""
    digest = hashlib.sha256(f"{mandate_id}|{intent_hash}|{attempt_no}".encode()).hexdigest()
    return f"ik_{digest[:32]}"


async def _call_with_retry[T](breaker: CircuitBreaker, fn: Callable[[], Awaitable[T]]) -> T:
    """§28 P5 instruction 2: explicit timeouts live on the adapter; retry
    classification and circuit-breaker behaviour live here, from the
    platform layer. Only TransientProviderError is ever retried -- a
    TerminalProviderError (a decline, a permanent rejection) propagates on
    the first attempt, and an open breaker's CircuitOpenError propagates
    immediately without burning a retry into a known-broken dependency."""

    async def _attempt() -> T:
        return await breaker.call(fn)

    return await retry_with_full_jitter(
        _attempt, max_attempts=settings.max_retry_attempts, retry_on=(TransientProviderError,)
    )


# ---------------------------------------------------------------------------
# S2 ORDER — idempotent order creation (§15.2, §7 steps 12-14)
# ---------------------------------------------------------------------------


async def create_provider_order(
    session_factory: async_sessionmaker[AsyncSession],
    provider: PaymentProvider,
    clock: Clock,
    breaker: CircuitBreaker,
    *,
    order_id: str,
    mandate_id: str,
    decision_id: str,
    quote_id: str,
    amount_minor: int,
    currency: str,
    attempt_no: int,
    intent_hash: str,
    actor_id: str = "system",
    sleep: _SleepFn = asyncio.sleep,
) -> tuple[OrderRecord, bool]:
    """Returns (order, was_duplicate). `was_duplicate` is True whenever
    this call did not itself create a new provider order — a genuine
    replay of a completed attempt, or a call that had to wait for a
    concurrent winner — matching §15.2's DUPLICATE_SUPPRESSED flag."""
    key = compute_idempotency_key(mandate_id, intent_hash, attempt_no)
    request_hash = hashlib.sha256(
        f"{order_id}|{mandate_id}|{amount_minor}|{currency}".encode()
    ).hexdigest()

    async with UnitOfWork(session_factory) as uow:
        existing = await uow.idempotency_keys.get(key)
        if existing is not None:
            if existing.state == "COMPLETED":
                order = await uow.orders.get_by_idempotency_key(key)
                assert order is not None
                return order, True
            if existing.state == "FAILED":
                raise IdempotentAttemptFailed(
                    f"idempotency key {key} previously failed", details=existing.response
                )
            # state == "IN_FLIGHT": someone else's attempt is still running.
            # The local order row may already exist but not yet carry a
            # provider_order_id (that lands in a second, later transaction)
            # -- returning it here would leak a half-finished result, so
            # this waits the same bounded poll a lost claim() race does.
            order = await _await_in_flight_completion(session_factory, key, sleep=sleep)
            return order, True

        won = await uow.idempotency_keys.claim(
            IdempotencyKeyRecord(
                key=key,
                request_hash=request_hash,
                state="IN_FLIGHT",
                expires_at=clock.now() + timedelta(seconds=settings.reservation_ttl_s),
            )
        )
        if not won:
            order = await _await_in_flight_completion(session_factory, key, sleep=sleep)
            return order, True

        # We won the claim. Local order row + write-ahead audit entry —
        # committed BEFORE the provider call (§7 step 13 / §11.2 G7).
        order = OrderRecord(
            id=order_id,
            mandate_id=mandate_id,
            decision_id=decision_id,
            quote_id=quote_id,
            status="CREATED",
            amount_minor=amount_minor,
            currency=currency,
            attempt_no=attempt_no,
            idempotency_key=key,
            created_at=clock.now(),
        )
        await uow.orders.add(order)
        await append_entry(
            uow,
            trace_id=new_id("trc"),
            actor_type="system",
            actor_id=actor_id,
            action=AuditAction.PAYMENT_INTENT,
            subject={"order_id": order_id, "mandate_id": mandate_id},
            payload={
                "order_id": order_id,
                "mandate_id": mandate_id,
                "decision_id": decision_id,
                "amount_minor": amount_minor,
                "currency": currency,
                "idempotency_key": key,
                "provider": settings.payment_provider,
                "mode": "test",
            },
        )
        await uow.commit()

    # ---- the single external call, outside any open transaction --------
    try:
        provider_order = await _call_with_retry(
            breaker,
            lambda: provider.create_order(
                amount_minor,
                currency,
                key,
                notes={"order_id": order_id, "mandate_id": mandate_id},
            ),
        )
    except (RetryExhausted, TerminalProviderError, CircuitOpenError) as exc:
        async with UnitOfWork(session_factory) as uow:
            await uow.payments.transition_status(
                order_id, "FAILED", updated_at=clock.now(), decline_reason=str(exc)[:500]
            )
            await uow.idempotency_keys.complete(
                key, state="FAILED", response={"error": str(exc)[:500]}
            )
            await append_entry(
                uow,
                trace_id=new_id("trc"),
                actor_type="system",
                actor_id=actor_id,
                action=AuditAction.PAYMENT_RESULT,
                subject={"order_id": order_id},
                payload={"status": "failed", "reason": "order_creation_failed"},
            )
            await uow.commit()
        raise

    async with UnitOfWork(session_factory) as uow:
        await uow.orders.set_provider_order_id(order_id, provider_order.id, updated_at=clock.now())
        await uow.idempotency_keys.complete(
            key,
            state="COMPLETED",
            response={"order_id": order_id, "provider_order_id": provider_order.id},
        )
        await uow.commit()
        final = await uow.orders.get(order_id)
        assert final is not None
        return final, False


async def _await_in_flight_completion(
    session_factory: async_sessionmaker[AsyncSession],
    key: str,
    *,
    sleep: _SleepFn,
    max_wait_s: float = 2.0,
    poll_interval_s: float = 0.05,
) -> OrderRecord:
    """§15.2: "zero rows means someone else owns this attempt." A bounded
    poll for that owner's result — never a second provider call."""
    waited = 0.0
    while waited < max_wait_s:
        async with UnitOfWork(session_factory) as uow:
            record = await uow.idempotency_keys.get(key)
            if record is not None and record.state == "COMPLETED":
                order = await uow.orders.get_by_idempotency_key(key)
                if order is not None:
                    return order
            if record is not None and record.state == "FAILED":
                raise IdempotentAttemptFailed(
                    f"idempotency key {key} previously failed", details=record.response
                )
        await sleep(poll_interval_s)
        waited += poll_interval_s
    raise IdempotencyInFlightTimeout(
        f"idempotency key {key} is still in flight after {max_wait_s}s"
    )


# ---------------------------------------------------------------------------
# S3/S4 AUTHORIZE + CAPTURE — signature-gated (§15.4)
# ---------------------------------------------------------------------------


async def verify_and_capture(
    uow: UnitOfWork,
    provider: PaymentProvider,
    clock: Clock,
    breaker: CircuitBreaker,
    *,
    order_id: str,
    provider_order_id: str,
    provider_payment_id: str,
    provider_signature: str,
    amount_minor: int,
    actor_id: str = "system",
) -> OrderRecord:
    """`capture()` is textually unreachable unless
    `verify_checkout_signature` returns True — see
    tests/integration/payments/test_checkout_signature.py for a spy-based
    proof that a tampered signature never calls capture()."""
    signature_hash = (
        f"sha256:{hashlib.sha256(provider_signature.encode()).hexdigest()}"
        if provider_signature
        else None
    )
    is_valid = provider.verify_checkout_signature(
        provider_order_id, provider_payment_id, provider_signature
    )
    if not is_valid:
        await uow.payments.transition_status(
            order_id,
            "FAILED",
            updated_at=clock.now(),
            decline_reason="invalid_checkout_signature",
        )
        await append_entry(
            uow,
            trace_id=new_id("trc"),
            actor_type="system",
            actor_id=actor_id,
            action=AuditAction.PAYMENT_RESULT,
            subject={"order_id": order_id},
            payload={
                "status": "failed",
                "reason": "invalid_checkout_signature",
                "signature_hash": signature_hash,
            },
        )
        await uow.commit()
        raise CheckoutSignatureInvalid(f"checkout signature invalid for order {order_id}")

    try:
        payment = await _call_with_retry(
            breaker, lambda: provider.capture(provider_payment_id, amount_minor)
        )
    except (RetryExhausted, TerminalProviderError, CircuitOpenError) as exc:
        await uow.payments.transition_status(
            order_id, "FAILED", updated_at=clock.now(), decline_reason=str(exc)[:500]
        )
        await append_entry(
            uow,
            trace_id=new_id("trc"),
            actor_type="system",
            actor_id=actor_id,
            action=AuditAction.PAYMENT_RESULT,
            subject={"order_id": order_id},
            payload={
                "status": "failed",
                "reason": "capture_failed",
                "signature_hash": signature_hash,
            },
        )
        await uow.commit()
        raise

    await uow.payments.transition_status(
        order_id, "CAPTURED", updated_at=clock.now(), provider_payment_id=payment.id
    )
    await append_entry(
        uow,
        trace_id=new_id("trc"),
        actor_type="system",
        actor_id=actor_id,
        action=AuditAction.PAYMENT_RESULT,
        subject={"order_id": order_id},
        payload={
            "status": "captured",
            "provider_payment_id": payment.id,
            "signature_hash": signature_hash,
        },
    )
    await uow.commit()
    order = await uow.orders.get(order_id)
    assert order is not None
    return order


# ---------------------------------------------------------------------------
# Webhooks (§15.3) — fast HTTP-path receipt, then worker-path processing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WebhookReceipt:
    outcome: Literal["accepted", "duplicate", "invalid_signature", "missing_event_id"]


async def process_webhook_delivery(
    uow: UnitOfWork,
    provider: PaymentProvider,
    *,
    raw_body: bytes,
    signature: str,
    event_id: str,
    event_type: str,
    payload: dict[str, object],
) -> WebhookReceipt:
    """The HTTP handler's entire job: verify (constant-time, in-memory,
    zero I/O) *before touching the database at all*, then persist for
    dedup (one indexed INSERT) + return. No order transition happens here
    — that is `process_unprocessed_webhooks`, on the worker.

    A missing, malformed, or invalid signature returns immediately with no
    database call whatsoever: no `webhook_events` row, no outbox row, no
    state transition, no worker work. This is the single verification path
    both the HTTP receiver and `actl replay-webhook` go through, so
    neither can accidentally diverge on this guarantee."""
    if not provider.verify_webhook(raw_body, signature):
        return WebhookReceipt(outcome="invalid_signature")
    if not event_id:
        # Can't dedupe safely without one; a valid signature alone is not
        # enough to accept a delivery we can never idempotently replay.
        return WebhookReceipt(outcome="missing_event_id")

    is_new = await uow.webhook_events.claim(
        WebhookEventRecord(
            provider_event_id=event_id,
            event_type=event_type,
            signature_valid=True,
            payload=payload,
        )
    )
    await uow.commit()
    return WebhookReceipt(outcome="accepted" if is_new else "duplicate")


async def process_unprocessed_webhooks(
    uow: UnitOfWork, clock: Clock, *, actor_id: str = "worker"
) -> list[str]:
    """The worker's queue: every claimed, signature-valid, not-yet-applied
    event. Re-running this over an already-processed event is a no-op
    twice over — `list_unprocessed` never returns it again, and even if it
    did, the terminal-status check inside `_apply_webhook_event` would
    refuse to re-apply it.

    §20 F10 / §28 P9 instruction 2: this is one of the worker's two
    money-affecting entry points -- refuses to apply any webhook (a
    capture/decline transition, real ledger movement) while the durable
    integrity halt is tripped."""
    await raise_if_halted(uow)
    processed_ids: list[str] = []
    for event in await uow.webhook_events.list_unprocessed():
        await _apply_webhook_event(uow, clock, event, actor_id=actor_id)
        await uow.webhook_events.mark_processed(event.provider_event_id, processed_at=clock.now())
        processed_ids.append(event.provider_event_id)
    await uow.commit()
    return processed_ids


async def _apply_webhook_event(
    uow: UnitOfWork, clock: Clock, event: WebhookEventRecord, *, actor_id: str
) -> None:
    payment_entity = _extract_payment_entity(event.payload)
    provider_order_id = payment_entity.get("order_id") if payment_entity else None
    provider_payment_id = payment_entity.get("id") if payment_entity else None
    if not provider_order_id:
        return

    order = await uow.payments.get_by_provider_order_id(cast(str, provider_order_id))
    if order is None or order.status in TERMINAL_STATUSES:
        return  # unknown order, or already settled — webhooks are evidence, never re-applied

    if event.event_type == "payment.captured":
        await uow.payments.transition_status(
            order.id,
            "CAPTURED",
            updated_at=clock.now(),
            provider_payment_id=str(provider_payment_id),
        )
        status = "captured"
    elif event.event_type == "payment.failed":
        await uow.payments.transition_status(
            order.id,
            "FAILED",
            updated_at=clock.now(),
            provider_payment_id=str(provider_payment_id) if provider_payment_id else None,
            decline_reason="payment.failed webhook",
        )
        status = "failed"
    else:
        return  # e.g. payment.authorized — not terminal, no transition needed

    await append_entry(
        uow,
        trace_id=new_id("trc"),
        actor_type="system",
        actor_id=actor_id,
        action=AuditAction.PAYMENT_RESULT,
        subject={"order_id": order.id},
        payload={"status": status, "source": "webhook", "provider_payment_id": provider_payment_id},
    )


def _extract_payment_entity(payload: dict[str, object]) -> dict[str, object] | None:
    body = payload.get("payload")
    if not isinstance(body, dict):
        return None
    payment = body.get("payment")
    if not isinstance(payment, dict):
        return None
    entity = payment.get("entity")
    return entity if isinstance(entity, dict) else None


# ---------------------------------------------------------------------------
# Reconciliation (§15.3 point 4, §20 F3) — the webhook that never arrives
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReconciliationOutcome:
    order_id: str
    action: Literal["captured", "failed", "still_pending", "poll_failed", "skipped"]


async def reconcile_non_terminal_orders(
    uow: UnitOfWork,
    provider: PaymentProvider,
    clock: Clock,
    breaker: CircuitBreaker,
    *,
    reconcile_after_s: int | None = None,
    actor_id: str = "reconciler",
) -> list[ReconciliationOutcome]:
    """§20 F10 / §28 P9 instruction 2: the worker's other money-affecting
    entry point -- refuses to settle any order from a provider poll while
    the durable integrity halt is tripped."""
    await raise_if_halted(uow)
    cutoff = clock.now() - timedelta(seconds=reconcile_after_s or settings.reconcile_after_s)
    outcomes: list[ReconciliationOutcome] = []
    for order in await uow.orders.list_non_terminal_older_than(cutoff):
        provider_order_id = order.provider_order_id
        if provider_order_id is None:
            outcomes.append(ReconciliationOutcome(order.id, "skipped"))
            continue

        async def _fetch(oid: str = provider_order_id) -> list[ProviderPayment]:
            return await provider.fetch_payments(oid)

        try:
            payments = await _call_with_retry(breaker, _fetch)
        except (RetryExhausted, TerminalProviderError, CircuitOpenError):
            outcomes.append(ReconciliationOutcome(order.id, "poll_failed"))
            continue

        latest = _latest_payment(payments)
        if latest is None:
            outcomes.append(ReconciliationOutcome(order.id, "still_pending"))
            continue

        if latest.status == "captured":
            await uow.payments.transition_status(
                order.id, "CAPTURED", updated_at=clock.now(), provider_payment_id=latest.id
            )
            await _audit_reconciled(uow, order.id, "captured", latest, actor_id)
            outcomes.append(ReconciliationOutcome(order.id, "captured"))
        elif latest.status == "failed":
            await uow.payments.transition_status(
                order.id,
                "FAILED",
                updated_at=clock.now(),
                provider_payment_id=latest.id,
                decline_reason=latest.error_code or "declined",
            )
            await _audit_reconciled(uow, order.id, "failed", latest, actor_id)
            outcomes.append(ReconciliationOutcome(order.id, "failed"))
        else:
            outcomes.append(ReconciliationOutcome(order.id, "still_pending"))

    await uow.commit()
    return outcomes


async def _audit_reconciled(
    uow: UnitOfWork, order_id: str, status: str, payment: ProviderPayment, actor_id: str
) -> None:
    await append_entry(
        uow,
        trace_id=new_id("trc"),
        actor_type="system",
        actor_id=actor_id,
        action=AuditAction.PAYMENT_RESULT,
        subject={"order_id": order_id},
        payload={"status": status, "source": "reconciler", "provider_payment_id": payment.id},
    )


def _latest_payment(payments: list[ProviderPayment]) -> ProviderPayment | None:
    if not payments:
        return None
    for wanted in ("captured", "failed"):
        for p in payments:
            if p.status == wanted:
                return p
    return payments[-1]
