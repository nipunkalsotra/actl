"""§15 compensation path (C1-C5), the pieces shared between the gate's own
self-compensation (S2 failing after S1 succeeded) and the saga
orchestrator's richer compensation sequences (S3/S4 failing after S2
succeeded). Each function here is idempotent and restart-safe: replaying
the same compensation for the same `ref_id`/mandate never double-releases,
double-marks, or forks the mandate's state.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application.audit_service import append_entry
from actl.application.ledger_service import release as ledger_release
from actl.application.ledger_service import reverse_settlement as ledger_reverse_settlement
from actl.application.payment_service import _call_with_retry
from actl.application.ports import PaymentProvider
from actl.domain.audit.events import AuditAction
from actl.domain.mandate.state_machine import MandateStatus, TransitionGuardContext, transition
from actl.infrastructure.db.repositories.orders import TERMINAL_STATUSES
from actl.infrastructure.db.uow import UnitOfWork
from actl.platform import metrics, tracing
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import Clock


async def release_reservation_and_mark_compensated(
    session_factory: async_sessionmaker[AsyncSession],
    clock: Clock,
    *,
    mandate_id: str,
    amount_minor: int,
    ref_id: str,
    actor_id: str,
    trace_id: str,
    reason: str,
) -> None:
    """C1 RELEASE, plus the mandate's EXECUTING -> COMPENSATED transition
    (§9.1) and the required `compensation.applied` audit entry. Safe to
    call more than once for the same `ref_id` (a crash-and-retry, or two
    callers racing to compensate the same failure) -- `ledger_release` is
    itself idempotent, and the mandate transition is skipped (not
    re-attempted) once the mandate is no longer EXECUTING."""
    metrics.compensations_total.labels(compensation="C1").inc()
    with tracing.span("compensation.C1_release_reservation", ref_id=ref_id, reason=reason):
        async with UnitOfWork(session_factory) as uow:
            released = await ledger_release(
                uow, clock, mandate_id=mandate_id, amount_minor=amount_minor, ref_id=ref_id
            )
            await append_entry(
                uow,
                trace_id=trace_id,
                actor_type="system",
                actor_id=actor_id,
                action=AuditAction.COMPENSATION_APPLIED,
                subject={"mandate_id": mandate_id, "ref_id": ref_id},
                payload={
                    "mandate_id": mandate_id,
                    "ref_id": ref_id,
                    "amount_minor": amount_minor,
                    "reason": reason,
                    "released": released,
                },
            )
            await _mark_compensated(uow, mandate_id)
            await uow.commit()


async def _mark_compensated(uow: UnitOfWork, mandate_id: str) -> None:
    current = await uow.mandates.get(mandate_id)
    if current is not None and current[1] is MandateStatus.EXECUTING:
        new_status = transition(
            MandateStatus.EXECUTING,
            "failure",
            TransitionGuardContext(all_compensations_confirmed=True),
        )
        await uow.mandates.update_status(mandate_id, new_status)


async def void_order_and_release_reservation(
    session_factory: async_sessionmaker[AsyncSession],
    clock: Clock,
    *,
    mandate_id: str,
    order_id: str,
    amount_minor: int,
    ref_id: str,
    actor_id: str,
    trace_id: str,
    reason: str,
) -> None:
    """C2 VOID then C1 RELEASE, strict reverse of the forward S1 RESERVE
    -> S2 ORDER pair (§15 compensation path). Order marked FAILED
    (abandoned, no capture ever attempted -- §15's "auth left to lapse");
    reservation released; mandate EXECUTING -> COMPENSATED. Idempotent:
    an already-terminal order/already-released reservation are each
    left alone."""
    metrics.compensations_total.labels(compensation="C2").inc()
    with tracing.span("compensation.C2_void_order", order_id=order_id, reason=reason):
        async with UnitOfWork(session_factory) as uow:
            order = await uow.orders.get(order_id)
            if order is not None and order.status not in TERMINAL_STATUSES:
                await uow.payments.transition_status(
                    order_id, "FAILED", updated_at=clock.now(), decline_reason=reason
                )
            released = await ledger_release(
                uow, clock, mandate_id=mandate_id, amount_minor=amount_minor, ref_id=ref_id
            )
            await append_entry(
                uow,
                trace_id=trace_id,
                actor_type="system",
                actor_id=actor_id,
                action=AuditAction.COMPENSATION_APPLIED,
                subject={"mandate_id": mandate_id, "order_id": order_id, "ref_id": ref_id},
                payload={
                    "mandate_id": mandate_id,
                    "order_id": order_id,
                    "ref_id": ref_id,
                    "amount_minor": amount_minor,
                    "reason": reason,
                    "released": released,
                },
            )
            await _mark_compensated(uow, mandate_id)
            await uow.commit()


async def refund_and_reverse_settlement(
    session_factory: async_sessionmaker[AsyncSession],
    provider: PaymentProvider,
    clock: Clock,
    breaker: CircuitBreaker,
    *,
    mandate_id: str,
    order_id: str,
    provider_payment_id: str,
    amount_minor: int,
    ref_id: str,
    actor_id: str,
    trace_id: str,
    reason: str,
) -> None:
    """C4 REFUND then C5 REVERSE, strict reverse of the forward S4 CAPTURE
    -> S5 SETTLE pair -- used when a capture has already succeeded at the
    provider but settlement could not be recorded locally. Idempotent:
    `provider.refund` is keyed by `ref_id` (§15.1 idempotency_key), and
    `ledger_reverse_settlement` is idempotent by `ref_id`."""
    metrics.compensations_total.labels(compensation="C4_C5").inc()
    with tracing.span("compensation.C4_C5_refund_and_reverse", order_id=order_id, reason=reason):
        with tracing.span("provider.refund"):
            await _call_with_retry(
                breaker,
                lambda: provider.refund(
                    provider_payment_id, amount_minor, f"ik_refund_{ref_id}"
                ),
            )

        async with UnitOfWork(session_factory) as uow:
            order = await uow.orders.get(order_id)
            # Unlike void_order_and_release_reservation's guard, CAPTURED is
            # *not* left alone here -- a refund's whole point is moving an
            # already-CAPTURED order to FAILED. Only an order already FAILED
            # (this same compensation, replayed) is a no-op.
            if order is not None and order.status != "FAILED":
                await uow.payments.transition_status(
                    order_id, "FAILED", updated_at=clock.now(), decline_reason=reason
                )
            reversed_ = await ledger_reverse_settlement(
                uow, clock, mandate_id=mandate_id, amount_minor=amount_minor, ref_id=ref_id
            )
            await append_entry(
                uow,
                trace_id=trace_id,
                actor_type="system",
                actor_id=actor_id,
                action=AuditAction.COMPENSATION_APPLIED,
                subject={"mandate_id": mandate_id, "order_id": order_id, "ref_id": ref_id},
                payload={
                    "mandate_id": mandate_id,
                    "order_id": order_id,
                    "ref_id": ref_id,
                    "amount_minor": amount_minor,
                    "reason": reason,
                    "reversed": reversed_,
                },
            )
            await _mark_compensated(uow, mandate_id)
            await uow.commit()
