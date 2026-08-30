"""§28 P6 exit criteria: test_gate_g4_no_overspend_under_concurrency (50
attempts, cap admits 3, exactly 3 allowed) -- through the *full* gate
(execute_money_action), not the ledger directly, so this also proves the
audit chain stays gapless/unforked under real concurrent G1-G7 traffic
against one mandate.

§28 P10 release-readiness correction: the three G6/G7 exception paths
`_run_g6_g7_execute` doesn't reach through any *sequential* replay --
`create_provider_order`'s own terminal-failure handling always
self-compensates the mandate to COMPENSATED before a caller ever sees the
result, so a second `execute_money_action` call with the same
(mandate_id, intent_hash, attempt_no) fails at G1 (MANDATE_INVALID) long
before it could reach G6/G7's idempotency handling. `IdempotentAttemptFailed`
and `IdempotencyInFlightTimeout` are therefore *concurrency-only* branches:
they exist for two genuinely simultaneous callers racing the same
idempotency key, never for a caller retrying after already seeing a
denial. `_BarrierProvider` below gives deterministic control over that
race (no sleep-and-hope timing) by blocking the winning call's own
provider call on an `asyncio.Event` until the losing call has
provably reached its own poll loop.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application.audit_service import verify_chain
from actl.application.gate import MoneyActionRequest, MoneyActionResult, execute_money_action
from actl.application.payment_service import IdempotencyInFlightTimeout, IdempotentAttemptFailed
from actl.application.ports import (
    ProviderOrder,
    ProviderPayment,
    ProviderRefund,
    TerminalProviderError,
)
from actl.domain.audit.events import AuditAction
from actl.domain.ledger.model import account, net_balance
from actl.domain.mandate.state_machine import MandateStatus
from actl.domain.policy.reason_codes import ReasonCode
from actl.infrastructure.db.uow import UnitOfWork
from actl.infrastructure.providers.simulator.adapter import SimulatorAdapter
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import SystemClock
from actl.platform.ids import new_id
from tests.integration.gate.conftest import seed_valid_gate_fixture

pytestmark = pytest.mark.asyncio(loop_scope="session")


class _BarrierProvider:
    """Wraps a real `SimulatorAdapter`: `create_order` signals
    `entered_event` (so a test knows the wrapped call is now blocked --
    which only happens *after* the caller's own idempotency-key claim has
    already committed, `create_provider_order`'s own "committed BEFORE
    the provider call" ordering), then waits on `release_event` before
    either raising `fail_with` or delegating to the real adapter."""

    def __init__(
        self,
        inner: SimulatorAdapter,
        entered_event: asyncio.Event,
        release_event: asyncio.Event,
        fail_with: Exception | None,
    ) -> None:
        self._inner = inner
        self._entered_event = entered_event
        self._release_event = release_event
        self._fail_with = fail_with

    async def create_order(
        self, amount_minor: int, currency: str, idempotency_key: str, notes: dict[str, str]
    ) -> ProviderOrder:
        self._entered_event.set()
        await self._release_event.wait()
        if self._fail_with is not None:
            raise self._fail_with
        return await self._inner.create_order(amount_minor, currency, idempotency_key, notes)

    async def fetch_payments(self, provider_order_id: str) -> list[ProviderPayment]:
        return await self._inner.fetch_payments(provider_order_id)

    async def capture(self, payment_id: str, amount_minor: int) -> ProviderPayment:
        return await self._inner.capture(payment_id, amount_minor)

    async def refund(
        self, payment_id: str, amount_minor: int, idempotency_key: str
    ) -> ProviderRefund:
        return await self._inner.refund(payment_id, amount_minor, idempotency_key)

    def verify_checkout_signature(self, order_id: str, payment_id: str, signature: str) -> bool:
        return self._inner.verify_checkout_signature(order_id, payment_id, signature)

    def verify_webhook(self, raw_body: bytes, signature: str) -> bool:
        return self._inner.verify_webhook(raw_body, signature)


class _RaisingProvider:
    """`create_order` always raises `to_raise` immediately -- no barrier,
    no concurrency, a single call is enough to exercise a genuinely
    unexpected (non-provider, non-idempotency) failure inside G6/G7."""

    def __init__(self, to_raise: Exception) -> None:
        self._to_raise = to_raise

    async def create_order(self, *args: Any, **kwargs: Any) -> ProviderOrder:
        raise self._to_raise

    async def fetch_payments(self, provider_order_id: str) -> list[ProviderPayment]:
        return []

    async def capture(self, payment_id: str, amount_minor: int) -> ProviderPayment:
        raise AssertionError("not reachable in this test")

    async def refund(
        self, payment_id: str, amount_minor: int, idempotency_key: str
    ) -> ProviderRefund:
        raise AssertionError("not reachable in this test")

    def verify_checkout_signature(self, order_id: str, payment_id: str, signature: str) -> bool:
        raise AssertionError("not reachable in this test")

    def verify_webhook(self, raw_body: bytes, signature: str) -> bool:
        raise AssertionError("not reachable in this test")


async def _reserved_balance(
    session_factory: async_sessionmaker[AsyncSession], mandate_id: str
) -> int:
    async with UnitOfWork(session_factory) as uow:
        entries = await uow.ledger_entries.list_for_account(account(mandate_id, "reserved"))
    return net_balance([(e.direction, e.amount_minor) for e in entries])


async def _mandate_status(
    session_factory: async_sessionmaker[AsyncSession], mandate_id: str
) -> MandateStatus:
    async with UnitOfWork(session_factory) as uow:
        current = await uow.mandates.get(mandate_id)
    assert current is not None
    return current[1]

N = 50
UNIT_MINOR = 300000  # make_locked_mandate()'s 900000 cap admits exactly 3
EXPECTED_ADMITTED = 3


async def _attempt(
    session_factory: async_sessionmaker[AsyncSession],
    provider: SimulatorAdapter,
    clock: SystemClock,
    breaker: CircuitBreaker,
    req: MoneyActionRequest,
) -> MoneyActionResult:
    return await execute_money_action(req, session_factory, provider, clock, breaker)


async def test_gate_g4_no_overspend_under_concurrency(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    provider = SimulatorAdapter(clock=clock)
    breaker = CircuitBreaker(name="razorpay", clock=clock)
    fixture = await seed_valid_gate_fixture(session_factory, clock)

    # tests/integration/audit's tamper-detection test deliberately corrupts
    # a row elsewhere in this same session-scoped chain (§28 P3) -- verify
    # only *this test's own* segment, not the whole shared chain from seq=1.
    async with UnitOfWork(session_factory) as uow:
        await uow.audit_log.acquire_chain_lock("actl.audit_log")
        start_tail = await uow.audit_log.get_tail()
    start_seq = start_tail[0] if start_tail is not None else 0

    reqs = [
        MoneyActionRequest(
            trace_id=new_id("trc"),
            mandate_id=fixture.mandate.mandate_id,
            decision_id=fixture.decision_id,
            quote_id=fixture.quote_id,
            intent_hash=fixture.intent_hash,
            amount_minor=UNIT_MINOR,
            currency="INR",
            attempt_no=attempt_no,
        )
        for attempt_no in range(1, N + 1)
    ]

    results = await asyncio.gather(
        *(_attempt(session_factory, provider, clock, breaker, req) for req in reqs)
    )

    admitted = sum(1 for r in results if r.verdict == "ALLOW")
    assert admitted == EXPECTED_ADMITTED, f"expected exactly {EXPECTED_ADMITTED}, got {admitted}"

    async with UnitOfWork(session_factory) as uow:
        await uow.audit_log.acquire_chain_lock("actl.audit_log")
        tail = await uow.audit_log.get_tail()
    assert tail is not None

    async with UnitOfWork(session_factory) as uow:
        verification = await verify_chain(uow, start_seq + 1, tail[0])
    assert verification.ok, verification.break_


# ---------------------------------------------------------------------------
# G6/G7 -- IdempotentAttemptFailed: a concurrent loser observes the winner's
# terminal failure while polling (§28 P10 release-readiness correction)
# ---------------------------------------------------------------------------


async def test_gate_g6_g7_concurrent_loser_sees_terminally_failed_key(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    fixture = await seed_valid_gate_fixture(session_factory, clock)

    entered = asyncio.Event()
    release = asyncio.Event()
    provider_a = _BarrierProvider(
        SimulatorAdapter(clock=clock), entered, release, fail_with=TerminalProviderError("boom")
    )
    provider_b = SimulatorAdapter(clock=clock)
    breaker_a = CircuitBreaker(name="a", clock=clock)
    breaker_b = CircuitBreaker(name="b", clock=clock)

    def _req(trace_prefix: str) -> MoneyActionRequest:
        return MoneyActionRequest(
            trace_id=new_id(trace_prefix),
            mandate_id=fixture.mandate.mandate_id,
            decision_id=fixture.decision_id,
            quote_id=fixture.quote_id,
            intent_hash=fixture.intent_hash,
            amount_minor=fixture.amount_minor,
            currency="INR",
            attempt_no=1,
        )

    req_a = _req("trc")
    req_b = _req("trc")
    task_a = asyncio.create_task(
        execute_money_action(req_a, session_factory, provider_a, clock, breaker_a)
    )
    # A's own idempotency-key claim is committed (create_provider_order's
    # own "committed BEFORE the provider call" ordering) by the time this
    # resolves -- A is now blocked inside create_order, not before.
    await asyncio.wait_for(entered.wait(), timeout=5)

    async def _release_after_delay() -> None:
        # B needs real wall-clock time to traverse its own G1-G5 (real DB
        # round trips) and lose the idempotency claim race against A's
        # already-committed IN_FLIGHT row, landing in its own poll loop --
        # generous margin, still far short of the 2s timeout Test 2
        # exercises. A background task (not B itself) times this release
        # so B's own call below is a plain, directly-awaited call, not a
        # second task -- matching the structure
        # test_gate_g6_g7_concurrent_loser_times_out_waiting already
        # proves stable.
        await asyncio.sleep(0.5)
        release.set()

    release_timer = asyncio.create_task(_release_after_delay())

    result_b = await execute_money_action(req_b, session_factory, provider_b, clock, breaker_b)
    result_a = await task_a
    await release_timer

    assert result_a.verdict == "DENY"
    assert result_a.reason_code == ReasonCode.PROVIDER_TERMINAL
    assert result_a.duplicate is False

    assert result_b.verdict == "DENY"
    assert result_b.reason_code == ReasonCode.PROVIDER_TERMINAL
    assert result_b.duplicate is True

    assert await _reserved_balance(session_factory, fixture.mandate.mandate_id) == 0
    assert (
        await _mandate_status(session_factory, fixture.mandate.mandate_id)
        == MandateStatus.COMPENSATED
    )

    # A's own trace_id links its write-ahead payment.intent, the
    # provider-failure payment.result, and the self-compensation's
    # compensation.applied -- the exact evidence trail a real terminally-
    # failed attempt leaves, regardless of which call (A or B) the caller
    # happens to be looking at.
    async with UnitOfWork(session_factory) as uow:
        entries = await uow.audit_log.get_by_trace_id(req_a.trace_id)
    actions = {e.action for e in entries}
    assert str(AuditAction.PAYMENT_INTENT) in actions, actions
    assert str(AuditAction.PAYMENT_RESULT) in actions, actions
    assert str(AuditAction.COMPENSATION_APPLIED) in actions, actions


# ---------------------------------------------------------------------------
# G6/G7 -- IdempotencyInFlightTimeout: a concurrent loser gives up waiting
# ---------------------------------------------------------------------------


async def test_gate_g6_g7_concurrent_loser_times_out_waiting(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    fixture = await seed_valid_gate_fixture(session_factory, clock)

    entered = asyncio.Event()
    release = asyncio.Event()
    provider_a = _BarrierProvider(
        SimulatorAdapter(clock=clock), entered, release, fail_with=TerminalProviderError("boom")
    )
    provider_b = SimulatorAdapter(clock=clock)
    breaker_a = CircuitBreaker(name="a", clock=clock)
    breaker_b = CircuitBreaker(name="b", clock=clock)

    def _req(trace_prefix: str) -> MoneyActionRequest:
        return MoneyActionRequest(
            trace_id=new_id(trace_prefix),
            mandate_id=fixture.mandate.mandate_id,
            decision_id=fixture.decision_id,
            quote_id=fixture.quote_id,
            intent_hash=fixture.intent_hash,
            amount_minor=fixture.amount_minor,
            currency="INR",
            attempt_no=1,
        )

    task_a = asyncio.create_task(
        execute_money_action(_req("trc"), session_factory, provider_a, clock, breaker_a)
    )
    await asyncio.wait_for(entered.wait(), timeout=5)

    # B starts racing, loses the claim, and polls -- A is deliberately kept
    # blocked (release is not set yet) past `_await_in_flight_completion`'s
    # own hardcoded max_wait_s=2.0s, so B must time out on its own.
    result_b = await execute_money_action(
        _req("trc"), session_factory, provider_b, clock, breaker_b
    )

    assert result_b.verdict == "DENY"
    assert result_b.reason_code == ReasonCode.PROVIDER_TRANSIENT
    assert result_b.duplicate is True

    # B's timeout is explicitly "outcome unknown, not a decline" -- A is
    # still in flight at this exact moment, so the reservation is by
    # design still held here, not yet released.
    assert (
        await _reserved_balance(session_factory, fixture.mandate.mandate_id)
        == fixture.amount_minor
    )

    release.set()  # let A finish (fail terminally, self-compensate) now
    result_a = await task_a

    assert result_a.verdict == "DENY"
    assert result_a.reason_code == ReasonCode.PROVIDER_TERMINAL

    # Once A has *also* resolved, the system reaches the safe terminal
    # state B's own denial could not yet promise on its own.
    assert await _reserved_balance(session_factory, fixture.mandate.mandate_id) == 0
    assert (
        await _mandate_status(session_factory, fixture.mandate.mandate_id)
        == MandateStatus.COMPENSATED
    )


# ---------------------------------------------------------------------------
# G6/G7 -- generic unexpected exception safety boundary
# ---------------------------------------------------------------------------


async def test_gate_g6_g7_unexpected_exception_still_self_compensates(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """§28 P10 release-readiness correction: this except-Exception branch
    previously returned DENY/INTERNAL_ERROR without releasing G4's
    reservation -- fixed in gate.py's own `_run_g6_g7_execute` to match
    its sibling branch. A raw, unclassified exception (never DBAPIError,
    never one of the provider-specific types) is what proves the branch
    the fix touched, not `retry_with_full_jitter`'s own DBAPIError retry."""
    clock = SystemClock()
    fixture = await seed_valid_gate_fixture(session_factory, clock)
    provider = _RaisingProvider(RuntimeError("simulated unexpected internal failure"))
    breaker = CircuitBreaker(name="c", clock=clock)

    req = MoneyActionRequest(
        trace_id=new_id("trc"),
        mandate_id=fixture.mandate.mandate_id,
        decision_id=fixture.decision_id,
        quote_id=fixture.quote_id,
        intent_hash=fixture.intent_hash,
        amount_minor=fixture.amount_minor,
        currency="INR",
        attempt_no=1,
    )
    result = await execute_money_action(req, session_factory, provider, clock, breaker)

    assert result.verdict == "DENY"
    assert result.reason_code == ReasonCode.INTERNAL_ERROR

    assert await _reserved_balance(session_factory, fixture.mandate.mandate_id) == 0
    assert (
        await _mandate_status(session_factory, fixture.mandate.mandate_id)
        == MandateStatus.COMPENSATED
    )

    async with UnitOfWork(session_factory) as uow:
        entries = await uow.audit_log.get_by_trace_id(req.trace_id)
    compensation = next(e for e in entries if e.action == AuditAction.COMPENSATION_APPLIED)
    assert compensation.payload.get("reason") == "unexpected_internal_failure"


# ---------------------------------------------------------------------------
# G6/G7 -- deterministic, non-concurrent closers for the same two
# exception branches the barrier-based tests above prove behaviorally.
#
# coverage.py's default core on Python 3.12 (sys.monitoring) -- and even
# the legacy ctrace engine pyproject.toml now forces -- has a real,
# empirically-confirmed measurement gap for lines executed inside an
# asyncio.create_task()-spawned coroutine racing a second concurrent task
# (a line proven to execute via a direct print() still intermittently
# reports "missing"). The barrier tests above are the genuine, real-race
# proof `IdempotentAttemptFailed`/`IdempotencyInFlightTimeout` behave
# correctly end to end; these two monkeypatch `create_provider_order`
# directly so `pytest --cov=actl.application.gate --cov-fail-under=100`
# is never at the mercy of that measurement flake.
# ---------------------------------------------------------------------------


async def test_gate_g6_g7_idempotent_attempt_failed_typed_result_deterministic(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    import actl.application.gate as gate_module

    clock = SystemClock()
    fixture = await seed_valid_gate_fixture(session_factory, clock)
    provider = SimulatorAdapter(clock=clock)
    breaker = CircuitBreaker(name="det-a", clock=clock)

    async def _fake_create_provider_order(*args: Any, **kwargs: Any) -> Any:
        raise IdempotentAttemptFailed("simulated: a sibling attempt already failed terminally")

    monkeypatch.setattr(gate_module, "create_provider_order", _fake_create_provider_order)

    req = MoneyActionRequest(
        trace_id=new_id("trc"),
        mandate_id=fixture.mandate.mandate_id,
        decision_id=fixture.decision_id,
        quote_id=fixture.quote_id,
        intent_hash=fixture.intent_hash,
        amount_minor=fixture.amount_minor,
        currency="INR",
        attempt_no=1,
    )
    result = await execute_money_action(req, session_factory, provider, clock, breaker)

    assert result.verdict == "DENY"
    assert result.reason_code == ReasonCode.PROVIDER_TERMINAL
    assert result.duplicate is True


async def test_gate_g6_g7_idempotency_in_flight_timeout_typed_result_deterministic(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    import actl.application.gate as gate_module

    clock = SystemClock()
    fixture = await seed_valid_gate_fixture(session_factory, clock)
    provider = SimulatorAdapter(clock=clock)
    breaker = CircuitBreaker(name="det-b", clock=clock)

    async def _fake_create_provider_order(*args: Any, **kwargs: Any) -> Any:
        raise IdempotencyInFlightTimeout("simulated: a sibling attempt is still in flight")

    monkeypatch.setattr(gate_module, "create_provider_order", _fake_create_provider_order)

    req = MoneyActionRequest(
        trace_id=new_id("trc"),
        mandate_id=fixture.mandate.mandate_id,
        decision_id=fixture.decision_id,
        quote_id=fixture.quote_id,
        intent_hash=fixture.intent_hash,
        amount_minor=fixture.amount_minor,
        currency="INR",
        attempt_no=1,
    )
    result = await execute_money_action(req, session_factory, provider, clock, breaker)

    assert result.verdict == "DENY"
    assert result.reason_code == ReasonCode.PROVIDER_TRANSIENT
    assert result.duplicate is True
