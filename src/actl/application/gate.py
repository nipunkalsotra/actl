"""§11 The Money Action Gate — the single entry point from a request to a
debit. Seven checks (G1-G7) run in a fixed, load-bearing order (§11
DESIGN RULE); every denial is a typed, reason-coded `MoneyActionResult`,
never an exception. G4 (row-locked budget reservation) must precede
EXECUTE; G7 (write-ahead audit) must be the last thing before it.

G6 (idempotency) + G7 (write-ahead audit) + EXECUTE are delegated to
`payment_service.create_provider_order` (§28 P5, already built and
tested) rather than re-implemented here — it already performs exactly
that sequence (local claim -> audit -> provider call) inside its own
two-phase-commit boundary. This module never imports a concrete payment
provider (`actl.infrastructure.providers.razorpay`); it receives the
injected `PaymentProvider` port and forwards it, same dependency shape as
`payment_service.py`. `tests/architecture/test_boundaries.py`'s
`test_only_gate_imports_payment_provider` is the executable guarantee
that this module is the only application code the architecture permits
to import the concrete adapter (§23.4) — nothing in this build actually
needs to, by construction, but this is the one place that constraint
would be checked against.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application.audit_service import append_entry
from actl.application.compensations import release_reservation_and_mark_compensated
from actl.application.ledger_service import reserve as ledger_reserve
from actl.application.payment_service import (
    IdempotencyInFlightTimeout,
    IdempotentAttemptFailed,
    compute_idempotency_key,
    create_provider_order,
)
from actl.application.ports import PaymentProvider, TerminalProviderError
from actl.config import settings
from actl.domain.audit.events import AuditAction
from actl.domain.mandate.hashing import verify_spec_hash
from actl.domain.mandate.signing import verify_signature
from actl.domain.mandate.state_machine import MandateStatus, TransitionGuardContext, transition
from actl.domain.policy.reason_codes import ReasonCode
from actl.infrastructure.db.uow import UnitOfWork
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import Clock
from actl.platform.errors import CircuitOpenError
from actl.platform.ids import new_id
from actl.platform.retry import RetryExhausted, retry_with_full_jitter


@dataclass(frozen=True)
class MoneyActionRequest:
    trace_id: str
    mandate_id: str
    decision_id: str
    quote_id: str
    intent_hash: str
    amount_minor: int
    currency: str
    attempt_no: int
    actor_id: str = "system"


@dataclass(frozen=True)
class MoneyActionResult:
    verdict: Literal["ALLOW", "DENY"]
    reason_code: ReasonCode
    trace_id: str
    order_id: str | None = None
    provider_order_id: str | None = None
    duplicate: bool = False


def _deny(code: ReasonCode, trace_id: str) -> MoneyActionResult:
    return MoneyActionResult(verdict="DENY", reason_code=code, trace_id=trace_id)


async def execute_money_action(
    req: MoneyActionRequest,
    session_factory: async_sessionmaker[AsyncSession],
    provider: PaymentProvider,
    clock: Clock,
    breaker: CircuitBreaker,
) -> MoneyActionResult:
    """G1-G7 in exact order (§11.1). Returns a typed `MoneyActionResult` on
    every path — malformed input, a mandate/decision/policy/budget/
    freshness/idempotency denial, a provider failure, or an unexpected
    internal error all become a safe typed result; nothing raises out of
    this function."""
    trace = req.trace_id
    if req.amount_minor <= 0 or not req.mandate_id or not req.decision_id or not req.quote_id:
        return _deny(ReasonCode.MALFORMED_REQUEST, trace)

    try:
        key = compute_idempotency_key(req.mandate_id, req.intent_hash, req.attempt_no)
        # A shared mandate row lock (G4) plus the audit chain's single
        # global advisory lock (G7's write-ahead entry, held inside the
        # same transaction) can deadlock under heavy same-mandate
        # concurrency -- Postgres detects and aborts one participant
        # rather than let it hang forever. That aborted transaction never
        # committed anything, so retrying it whole, from a fresh
        # UnitOfWork, is always safe.
        gate_result = await retry_with_full_jitter(
            lambda: _attempt_g1_through_g5(req, session_factory, clock, key),
            max_attempts=50,
            base_delay_s=0.01,
            max_delay_s=0.5,
            retry_on=(DBAPIError,),
        )
    except Exception:
        return _deny(ReasonCode.INTERNAL_ERROR, trace)

    if gate_result is not None:
        return gate_result  # deny -- transaction rolled back, nothing durable

    return await _run_g6_g7_execute(req, session_factory, provider, clock, breaker, key)


async def _attempt_g1_through_g5(
    req: MoneyActionRequest,
    session_factory: async_sessionmaker[AsyncSession],
    clock: Clock,
    key: str,
) -> MoneyActionResult | None:
    async with UnitOfWork(session_factory) as uow:
        gate_result = await _run_g1_through_g5(req, uow, clock, key)
        if gate_result is not None:
            return gate_result
        await uow.commit()  # G1-G5 all passed: reservation + EXECUTING durable now
    return None


async def _run_g1_through_g5(
    req: MoneyActionRequest, uow: UnitOfWork, clock: Clock, key: str
) -> MoneyActionResult | None:
    """Returns a DENY result to short-circuit, or None if every gate
    passed (caller commits). Runs entirely inside one transaction so a
    deny after G4's reservation insert rolls that insert back too --
    nothing durable happens on any path through this function except the
    all-pass path the caller commits."""
    trace = req.trace_id

    # G1 -- mandate validity, re-read from the DB, never a passed-in copy.
    got = await uow.mandates.get(req.mandate_id)
    if got is None:
        return _deny(ReasonCode.MANDATE_INVALID, trace)
    mandate, status = got
    if status is MandateStatus.REVOKED:
        return _deny(ReasonCode.MANDATE_REVOKED, trace)
    if status not in (MandateStatus.LOCKED, MandateStatus.EXECUTING):
        # EXECUTING is admitted alongside LOCKED: a mandate's first
        # successful money action already advanced it past LOCKED, so a
        # replay of that same attempt (G6) -- or a genuinely later attempt
        # under max_transactions > 1 -- must still be able to reach G6/G7,
        # not be rejected here just because it isn't the mandate's first
        # pass through this gate.
        return _deny(ReasonCode.MANDATE_INVALID, trace)
    if not verify_spec_hash(mandate):
        return _deny(ReasonCode.MANDATE_TAMPERED, trace)
    if mandate.spec_hash is None or mandate.signature is None:
        return _deny(ReasonCode.MANDATE_UNSIGNED, trace)
    if not verify_signature(
        mandate.spec_hash, settings.mandate_signing_key.encode("utf-8"), mandate.signature.value
    ):
        return _deny(ReasonCode.MANDATE_UNSIGNED, trace)
    if clock.now() >= mandate.temporal.expires_at:
        return _deny(ReasonCode.MANDATE_EXPIRED, trace)

    # G2 -- the decision must be bound to THIS intent and be fresh.
    decision = await uow.decisions.get(req.decision_id)
    if decision is None or decision.intent_hash != req.intent_hash:
        return _deny(ReasonCode.INTENT_MISMATCH, trace)
    if decision.mandate_spec_hash != mandate.spec_hash:
        return _deny(ReasonCode.INTENT_MISMATCH, trace)
    if clock.now() - decision.evaluated_at > timedelta(seconds=decision.ttl_s):
        return _deny(ReasonCode.DECISION_STALE, trace)

    # G3 -- the verdict itself. The specific failing rule's own reason
    # code is more informative than a generic POLICY_DENY bucket, and
    # every code in decision.reason_codes is already drawn from this same
    # closed registry (Pydantic-typed on DecisionRecord).
    if decision.verdict != "ALLOW":
        return _deny(decision.reason_codes[0], trace)

    # G4 -- atomic budget reservation. Row lock (inside ledger_reserve)
    # makes concurrent overspend across this mandate impossible.
    reservation = await ledger_reserve(
        uow,
        clock,
        mandate_id=req.mandate_id,
        amount_minor=req.amount_minor,
        max_total_minor=mandate.bounds.max_total_minor,
        ref_id=key,
    )
    if reservation is None:
        return _deny(ReasonCode.BUDGET_EXCEEDED, trace)

    # G5 -- freshness: the quote must still be live and the catalog unchanged.
    quote = await uow.quotes.get(req.quote_id)
    if quote is None or quote.expires_at <= clock.now():
        return _deny(ReasonCode.QUOTE_EXPIRED, trace)
    if quote.catalog_version != await uow.catalog.current_version():
        return _deny(ReasonCode.STALE_PRICE, trace)

    # All seven checks reachable from here pass -- LOCKED -> EXECUTING,
    # budget reserved, saga instantiated (§9.1). Only fire the transition
    # on the mandate's first pass through the gate (status still LOCKED);
    # a replay or a later attempt against an already-EXECUTING mandate
    # leaves the status alone -- there is no LOCKED-only transition to
    # repeat, and none is needed.
    if status is MandateStatus.LOCKED:
        new_status = transition(
            status,
            "propose",
            TransitionGuardContext(
                now=clock.now(),
                expires_at=mandate.temporal.expires_at,
                txn_count=0,
                max_transactions=mandate.bounds.max_transactions,
            ),
        )
        await uow.mandates.update_status(req.mandate_id, new_status)
    await append_entry(
        uow,
        trace_id=trace,
        actor_type="system",
        actor_id=req.actor_id,
        action=AuditAction.BUDGET_RESERVED,
        subject={"mandate_id": req.mandate_id, "ref_id": key},
        payload={
            "mandate_id": req.mandate_id,
            "ref_id": key,
            "amount_minor": req.amount_minor,
            "currency": req.currency,
        },
    )
    return None


async def _run_g6_g7_execute(
    req: MoneyActionRequest,
    session_factory: async_sessionmaker[AsyncSession],
    provider: PaymentProvider,
    clock: Clock,
    breaker: CircuitBreaker,
    key: str,
) -> MoneyActionResult:
    trace = req.trace_id
    order_id = new_id("ord")
    try:
        # Same deadlock-retry reasoning as G1-G5 (§11 DESIGN RULE comment
        # above _attempt_g1_through_g5): create_provider_order's own
        # write-ahead audit entry (G7) can collide with a concurrent
        # caller's under heavy same-mandate load. A deadlock there always
        # aborts before create_provider_order's first transaction commits
        # (nothing durable yet), so retrying the whole call is safe.
        order, was_duplicate = await retry_with_full_jitter(
            lambda: create_provider_order(
                session_factory,
                provider,
                clock,
                breaker,
                order_id=order_id,
                mandate_id=req.mandate_id,
                decision_id=req.decision_id,
                quote_id=req.quote_id,
                amount_minor=req.amount_minor,
                currency=req.currency,
                attempt_no=req.attempt_no,
                intent_hash=req.intent_hash,
                actor_id=req.actor_id,
            ),
            max_attempts=50,
            base_delay_s=0.01,
            max_delay_s=0.5,
            retry_on=(DBAPIError,),
        )
    except IdempotentAttemptFailed:
        # A pure replay of an attempt that already failed terminally --
        # C1 already ran for the *original* attempt; replaying must never
        # release (or touch) the ledger a second time.
        return MoneyActionResult(
            verdict="DENY", reason_code=ReasonCode.PROVIDER_TERMINAL, trace_id=trace, duplicate=True
        )
    except IdempotencyInFlightTimeout:
        # Outcome unknown -- a sibling attempt is still running. Safe to
        # retry later; never treated as a decline.
        return MoneyActionResult(
            verdict="DENY",
            reason_code=ReasonCode.PROVIDER_TRANSIENT,
            trace_id=trace,
            duplicate=True,
        )
    except (RetryExhausted, TerminalProviderError, CircuitOpenError) as exc:
        # S2 failed durably after S1's reservation already committed --
        # self-compensate (C1) before returning, so no reservation is
        # ever left dangling from a single execute_money_action() call.
        await release_reservation_and_mark_compensated(
            session_factory,
            clock,
            mandate_id=req.mandate_id,
            amount_minor=req.amount_minor,
            ref_id=key,
            actor_id=req.actor_id,
            trace_id=trace,
            reason="order_creation_failed",
        )
        code = (
            ReasonCode.PROVIDER_TERMINAL
            if isinstance(exc, TerminalProviderError)
            else ReasonCode.PROVIDER_TRANSIENT
        )
        return MoneyActionResult(verdict="DENY", reason_code=code, trace_id=trace)
    except Exception:
        return MoneyActionResult(
            verdict="DENY", reason_code=ReasonCode.INTERNAL_ERROR, trace_id=trace
        )

    return MoneyActionResult(
        verdict="ALLOW",
        reason_code=ReasonCode.OK,
        trace_id=trace,
        order_id=order.id,
        provider_order_id=order.provider_order_id,
        duplicate=was_duplicate,
    )
