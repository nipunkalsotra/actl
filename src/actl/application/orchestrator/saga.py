"""§11/§15 payment saga orchestrator: S1-S5 forward, C1-C5 compensation,
strict reverse. Saga state is persisted in Postgres (`sagas`) before every
side effect (§15 "Durability guarantees") -- a crash between any two steps
resumes safely on the next call for the same (mandate, intent, attempt_no),
since every step this module calls into (the gate, `payment_service`,
`ledger_service`, the compensation helpers) is itself idempotent by the
same `ref_id`/idempotency key.

Two entry points, matching the real S1/S2 vs S3/S4/S5 split (§15.4: the
gate ends at a pending Order; completing the sale needs the payer's own,
later, out-of-band checkout authorization):

  `begin_purchase`    -- S1 RESERVE + S2 ORDER, via `application.gate`.
  `complete_purchase` -- S3 AUTHORIZE + S4 CAPTURE + S5 SETTLE, given the
                         checkout outcome (a real browser callback in
                         production; the SimulatorAdapter's deterministic
                         equivalent in every automated test, §28 P6
                         instruction 4's "use the simulator for saga
                         failure-path tests; do not create real Razorpay
                         payments during normal tests").
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application.audit_service import append_entry
from actl.application.compensations import (
    refund_and_reverse_settlement,
    void_order_and_release_reservation,
)
from actl.application.gate import MoneyActionRequest, execute_money_action
from actl.application.ledger_service import capture as ledger_capture
from actl.application.payment_service import (
    CheckoutSignatureInvalid,
    compute_idempotency_key,
    verify_and_capture,
)
from actl.application.ports import PaymentProvider, TerminalProviderError
from actl.domain.audit.events import AuditAction
from actl.domain.mandate.state_machine import MandateStatus, TransitionGuardContext, transition
from actl.domain.policy.reason_codes import ReasonCode
from actl.infrastructure.db.repositories.sagas import SagaRecord
from actl.infrastructure.db.uow import UnitOfWork
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import Clock
from actl.platform.errors import CircuitOpenError
from actl.platform.ids import new_id
from actl.platform.retry import RetryExhausted


@dataclass(frozen=True)
class SagaSnapshot:
    saga_id: str
    mandate_id: str
    status: str
    step: str
    order_id: str | None
    reason_code: ReasonCode | None = None


class SagaNotFound(Exception):
    pass


def _snapshot(record: SagaRecord, reason_code: ReasonCode | None = None) -> SagaSnapshot:
    return SagaSnapshot(
        saga_id=record.id,
        mandate_id=record.mandate_id,
        status=record.status,
        step=record.step,
        order_id=record.order_id,
        reason_code=reason_code,
    )


async def begin_purchase(
    req: MoneyActionRequest,
    session_factory: async_sessionmaker[AsyncSession],
    provider: PaymentProvider,
    clock: Clock,
    breaker: CircuitBreaker,
) -> SagaSnapshot:
    """S1 RESERVE + S2 ORDER. Restart-safe: a saga row already present and
    not RUNNING is a resolved result returned as-is, never re-executed
    (no duplicate reservation, no duplicate order); one still RUNNING (a
    crash between persisting it and the gate call returning) is safe to
    resume by simply calling the gate again -- every check inside it is
    idempotent by the same key this saga row is keyed by."""
    key = compute_idempotency_key(req.mandate_id, req.intent_hash, req.attempt_no)

    async with UnitOfWork(session_factory) as uow:
        existing = await uow.sagas.get(key)
        if existing is not None and existing.status != "RUNNING":
            return _snapshot(existing)
        if existing is None:
            await uow.sagas.add(
                SagaRecord(
                    id=key,
                    mandate_id=req.mandate_id,
                    decision_id=req.decision_id,
                    quote_id=req.quote_id,
                    amount_minor=req.amount_minor,
                    currency=req.currency,
                    step="S1_RESERVE",
                    status="RUNNING",
                ),
                created_at=clock.now(),
            )
            await uow.commit()

    result = await execute_money_action(req, session_factory, provider, clock, breaker)

    async with UnitOfWork(session_factory) as uow:
        if result.verdict == "ALLOW":
            await uow.sagas.update(
                key,
                step="S3_AUTHORIZE",
                status="AWAITING_AUTHORIZATION",
                updated_at=clock.now(),
                order_id=result.order_id,
            )
        else:
            await uow.sagas.update(
                key,
                step=f"DENY_{result.reason_code.value}",
                status="FAILED",
                updated_at=clock.now(),
            )
        await uow.commit()
        final = await uow.sagas.get(key)
        assert final is not None

    return _snapshot(final, reason_code=None if result.verdict == "ALLOW" else result.reason_code)


async def complete_purchase(
    saga_id: str,
    session_factory: async_sessionmaker[AsyncSession],
    provider: PaymentProvider,
    clock: Clock,
    breaker: CircuitBreaker,
    *,
    provider_order_id: str,
    provider_payment_id: str,
    provider_signature: str,
    actor_id: str = "system",
) -> SagaSnapshot:
    """S3 AUTHORIZE + S4 CAPTURE + S5 SETTLE. Idempotent/restart-safe: a
    saga not in AWAITING_AUTHORIZATION is already resolved and is
    returned as-is without repeating any step (no duplicate capture, no
    duplicate settle, no duplicate compensation)."""
    async with UnitOfWork(session_factory) as uow:
        saga = await uow.sagas.get(saga_id)
    if saga is None:
        raise SagaNotFound(saga_id)
    if saga.status != "AWAITING_AUTHORIZATION":
        return _snapshot(saga)
    assert saga.order_id is not None
    order_id = saga.order_id
    trace_id = new_id("trc")

    # Kill-switch (§9.1: "any -> REVOKED ... in-flight saga halted at the
    # next safe point; reservations released"). Checked before S3/S4 so a
    # revocation that lands between begin_purchase and complete_purchase
    # is honoured rather than racing an in-flight authorization to completion.
    async with UnitOfWork(session_factory) as uow:
        current = await uow.mandates.get(saga.mandate_id)
    if current is not None and current[1] is MandateStatus.REVOKED:
        await void_order_and_release_reservation(
            session_factory,
            clock,
            mandate_id=saga.mandate_id,
            order_id=order_id,
            amount_minor=saga.amount_minor,
            ref_id=saga_id,
            actor_id=actor_id,
            trace_id=trace_id,
            reason="mandate_revoked",
        )
        return await _finalize(
            session_factory, saga_id, step="C2_VOID", status="COMPENSATED", clock=clock
        )

    # S3 AUTHORIZE -- the test credential (or the real payer's bank) drove
    # success or decline at S2's create_order; check that outcome before
    # ever attempting a signature-gated capture.
    payments = await provider.fetch_payments(provider_order_id)
    declined = any(p.id == provider_payment_id and p.status == "failed" for p in payments)
    if declined:
        await void_order_and_release_reservation(
            session_factory,
            clock,
            mandate_id=saga.mandate_id,
            order_id=order_id,
            amount_minor=saga.amount_minor,
            ref_id=saga_id,
            actor_id=actor_id,
            trace_id=trace_id,
            reason="payment_declined",
        )
        return await _finalize(
            session_factory, saga_id, step="C2_VOID", status="COMPENSATED", clock=clock
        )

    # S4 CAPTURE -- signature-gated (§15.4), reusing the already-tested P5 path.
    try:
        async with UnitOfWork(session_factory) as uow:
            await verify_and_capture(
                uow,
                provider,
                clock,
                breaker,
                order_id=order_id,
                provider_order_id=provider_order_id,
                provider_payment_id=provider_payment_id,
                provider_signature=provider_signature,
                amount_minor=saga.amount_minor,
                actor_id=actor_id,
            )
    except CheckoutSignatureInvalid:
        await void_order_and_release_reservation(
            session_factory,
            clock,
            mandate_id=saga.mandate_id,
            order_id=order_id,
            amount_minor=saga.amount_minor,
            ref_id=saga_id,
            actor_id=actor_id,
            trace_id=trace_id,
            reason="invalid_checkout_signature",
        )
        return await _finalize(
            session_factory, saga_id, step="C2_VOID", status="COMPENSATED", clock=clock
        )
    except (RetryExhausted, TerminalProviderError, CircuitOpenError):
        await void_order_and_release_reservation(
            session_factory,
            clock,
            mandate_id=saga.mandate_id,
            order_id=order_id,
            amount_minor=saga.amount_minor,
            ref_id=saga_id,
            actor_id=actor_id,
            trace_id=trace_id,
            reason="capture_failed",
        )
        return await _finalize(
            session_factory, saga_id, step="C2_VOID", status="COMPENSATED", clock=clock
        )

    # S5 SETTLE -- ledger reserved -> settled, mandate EXECUTING -> SETTLED,
    # audit close. If the ledger can't record the capture that already
    # happened at the provider (should not occur given the
    # AWAITING_AUTHORIZATION guard above, but money-correctness never
    # assumes "should not occur") -- C4 REFUND + C5 REVERSE undo it rather
    # than reporting a false settlement.
    async with UnitOfWork(session_factory) as uow:
        captured = await ledger_capture(
            uow, clock, mandate_id=saga.mandate_id, amount_minor=saga.amount_minor, ref_id=saga_id
        )
        if not captured:
            await uow.commit()
            await refund_and_reverse_settlement(
                session_factory,
                provider,
                clock,
                breaker,
                mandate_id=saga.mandate_id,
                order_id=order_id,
                provider_payment_id=provider_payment_id,
                amount_minor=saga.amount_minor,
                ref_id=saga_id,
                actor_id=actor_id,
                trace_id=trace_id,
                reason="ledger_capture_failed_after_provider_capture",
            )
            return await _finalize(
                session_factory, saga_id, step="C5_REVERSE", status="COMPENSATED", clock=clock
            )

        current = await uow.mandates.get(saga.mandate_id)
        if current is not None and current[1] is MandateStatus.EXECUTING:
            new_status = transition(
                MandateStatus.EXECUTING,
                "captured",
                TransitionGuardContext(
                    reserved_minor=saga.amount_minor, captured_minor=saga.amount_minor
                ),
            )
            await uow.mandates.update_status(saga.mandate_id, new_status)
        await append_entry(
            uow,
            trace_id=trace_id,
            actor_type="system",
            actor_id=actor_id,
            action=AuditAction.SETTLEMENT_CLOSED,
            subject={"order_id": order_id, "mandate_id": saga.mandate_id},
            payload={
                "order_id": order_id,
                "mandate_id": saga.mandate_id,
                "amount_minor": saga.amount_minor,
                "provider_payment_id": provider_payment_id,
            },
        )
        await uow.sagas.update(
            saga_id, step="S5_SETTLE", status="COMPLETED", updated_at=clock.now()
        )
        await uow.commit()
        final = await uow.sagas.get(saga_id)
        assert final is not None

    return _snapshot(final)


async def _finalize(
    session_factory: async_sessionmaker[AsyncSession],
    saga_id: str,
    *,
    step: str,
    status: str,
    clock: Clock,
) -> SagaSnapshot:
    async with UnitOfWork(session_factory) as uow:
        await uow.sagas.update(saga_id, step=step, status=status, updated_at=clock.now())
        await uow.commit()
        final = await uow.sagas.get(saga_id)
        assert final is not None
    return _snapshot(final)
