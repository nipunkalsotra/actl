"""§28 P6 exit criteria: one test per gate (G1-G7) for both outcomes where
applicable, gate order, typed-result/no-exception-escape, and idempotent
replay. Real Postgres (§18.1); the SimulatorAdapter drives every payment
outcome deterministically (§28 P5/P6: never a real Razorpay call in the
normal suite).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application.gate import MoneyActionRequest, execute_money_action
from actl.application.ledger_service import _state_of
from actl.domain.ledger.model import ReservationState, account
from actl.domain.mandate.hashing import compute_spec_hash
from actl.domain.mandate.models import MandateSignature
from actl.domain.mandate.signing import sign_spec_hash
from actl.domain.mandate.state_machine import MandateStatus
from actl.domain.policy.decision import DecisionRecord
from actl.domain.policy.reason_codes import ReasonCode
from actl.infrastructure.db.uow import UnitOfWork
from actl.infrastructure.providers.simulator.adapter import Scenario, SimulatorAdapter
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import SystemClock
from actl.platform.ids import new_id
from tests.integration.db.conftest import make_locked_mandate
from tests.integration.gate.conftest import (
    GateFixture,
    fake_hash,
    seed_decision,
    seed_mandate,
    seed_quote,
    seed_valid_gate_fixture,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _req(
    fixture: GateFixture, *, attempt_no: int = 1, amount_minor: int | None = None
) -> MoneyActionRequest:
    return MoneyActionRequest(
        trace_id=new_id("trc"),
        mandate_id=fixture.mandate.mandate_id,
        decision_id=fixture.decision_id,
        quote_id=fixture.quote_id,
        intent_hash=fixture.intent_hash,
        amount_minor=amount_minor if amount_minor is not None else fixture.amount_minor,
        currency="INR",
        attempt_no=attempt_no,
    )


def _provider_and_breaker(clock: SystemClock) -> tuple[SimulatorAdapter, CircuitBreaker]:
    return SimulatorAdapter(clock=clock), CircuitBreaker(name="razorpay", clock=clock)


async def _reserved_balance(
    session_factory: async_sessionmaker[AsyncSession], mandate_id: str
) -> int:
    from actl.domain.ledger.model import net_balance

    async with UnitOfWork(session_factory) as uow:
        entries = await uow.ledger_entries.list_for_account(account(mandate_id, "reserved"))
    return net_balance([(e.direction, e.amount_minor) for e in entries])


# ---------------------------------------------------------------------------
# G1 -- mandate validity
# ---------------------------------------------------------------------------


async def test_gate_g1_allows_a_valid_locked_mandate(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    fixture = await seed_valid_gate_fixture(session_factory, clock)
    provider, breaker = _provider_and_breaker(clock)

    result = await execute_money_action(_req(fixture), session_factory, provider, clock, breaker)

    assert result.verdict == "ALLOW"
    assert result.reason_code == ReasonCode.OK


async def test_gate_g1_allows_a_second_attempt_against_an_already_executing_mandate(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A mandate already EXECUTING (its first money action already
    succeeded) is not automatically MANDATE_INVALID -- a distinct, later
    attempt_no with room left under the cap still reaches G7/EXECUTE."""
    clock = SystemClock()
    mandate = make_locked_mandate()
    await seed_mandate(session_factory, mandate, status=MandateStatus.EXECUTING)
    intent_hash = fake_hash(new_id("intent"))
    decision_id = await seed_decision(
        session_factory, clock, mandate=mandate, intent_hash=intent_hash, verdict="ALLOW"
    )
    quote_id = await seed_quote(session_factory, clock, mandate_id=mandate.mandate_id)
    fixture = GateFixture(mandate, decision_id, quote_id, intent_hash, 300000)
    provider, breaker = _provider_and_breaker(clock)

    result = await execute_money_action(
        _req(fixture, attempt_no=2), session_factory, provider, clock, breaker
    )

    assert result.verdict == "ALLOW"


async def test_gate_g1_rejects_unknown_mandate(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    fixture = await seed_valid_gate_fixture(session_factory, clock)
    provider, breaker = _provider_and_breaker(clock)
    req = replace(_req(fixture), mandate_id="mdt_does_not_exist")

    result = await execute_money_action(req, session_factory, provider, clock, breaker)

    assert result.verdict == "DENY"
    assert result.reason_code == ReasonCode.MANDATE_INVALID


async def test_gate_g1_rejects_a_settled_mandate(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """EXECUTING is deliberately admitted (a replay or a later attempt
    under max_transactions > 1) -- but a terminal status like SETTLED
    never is."""
    clock = SystemClock()
    mandate = make_locked_mandate()
    await seed_mandate(session_factory, mandate, status=MandateStatus.SETTLED)
    intent_hash = fake_hash(new_id("intent"))
    decision_id = await seed_decision(
        session_factory, clock, mandate=mandate, intent_hash=intent_hash, verdict="ALLOW"
    )
    quote_id = await seed_quote(session_factory, clock, mandate_id=mandate.mandate_id)
    fixture = GateFixture(mandate, decision_id, quote_id, intent_hash, 840000)
    provider, breaker = _provider_and_breaker(clock)

    result = await execute_money_action(_req(fixture), session_factory, provider, clock, breaker)

    assert result.verdict == "DENY"
    assert result.reason_code == ReasonCode.MANDATE_INVALID


async def test_gate_g1_rejects_a_revoked_mandate(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    mandate = make_locked_mandate()
    await seed_mandate(session_factory, mandate, status=MandateStatus.REVOKED)
    intent_hash = fake_hash(new_id("intent"))
    decision_id = await seed_decision(
        session_factory, clock, mandate=mandate, intent_hash=intent_hash, verdict="ALLOW"
    )
    quote_id = await seed_quote(session_factory, clock, mandate_id=mandate.mandate_id)
    fixture = GateFixture(mandate, decision_id, quote_id, intent_hash, 840000)
    provider, breaker = _provider_and_breaker(clock)

    result = await execute_money_action(_req(fixture), session_factory, provider, clock, breaker)

    assert result.verdict == "DENY"
    assert result.reason_code == ReasonCode.MANDATE_REVOKED


async def test_gate_g1_rejects_a_tampered_mandate(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    mandate = make_locked_mandate()
    tampered = mandate.model_copy(
        update={"bounds": mandate.bounds.model_copy(update={"max_total_minor": 99_000_000})}
    )
    await seed_mandate(session_factory, tampered)
    intent_hash = fake_hash(new_id("intent"))
    decision_id = await seed_decision(
        session_factory, clock, mandate=tampered, intent_hash=intent_hash, verdict="ALLOW"
    )
    quote_id = await seed_quote(session_factory, clock, mandate_id=tampered.mandate_id)
    fixture = GateFixture(tampered, decision_id, quote_id, intent_hash, 840000)
    provider, breaker = _provider_and_breaker(clock)

    result = await execute_money_action(_req(fixture), session_factory, provider, clock, breaker)

    assert result.verdict == "DENY"
    assert result.reason_code == ReasonCode.MANDATE_TAMPERED


async def test_gate_g1_rejects_a_mandate_signed_with_the_wrong_key(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    mandate = make_locked_mandate()
    forged_sig = MandateSignature(
        alg="HMAC-SHA256",
        key_id="mk_1",
        value=sign_spec_hash(mandate.spec_hash or "", b"wrong-key"),
    )
    forged = mandate.model_copy(update={"signature": forged_sig})
    assert (
        compute_spec_hash(forged) == forged.spec_hash
    )  # hash still matches -- only the sig is bad
    await seed_mandate(session_factory, forged)
    intent_hash = fake_hash(new_id("intent"))
    decision_id = await seed_decision(
        session_factory, clock, mandate=forged, intent_hash=intent_hash, verdict="ALLOW"
    )
    quote_id = await seed_quote(session_factory, clock, mandate_id=forged.mandate_id)
    fixture = GateFixture(forged, decision_id, quote_id, intent_hash, 840000)
    provider, breaker = _provider_and_breaker(clock)

    result = await execute_money_action(_req(fixture), session_factory, provider, clock, breaker)

    assert result.verdict == "DENY"
    assert result.reason_code == ReasonCode.MANDATE_UNSIGNED


async def test_gate_g1_rejects_a_mandate_with_no_signature_at_all(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """§28 P10 release-readiness correction: the wrong-key test above
    exercises `verify_signature(...)` returning False; this exercises the
    *other*, textually-earlier MANDATE_UNSIGNED check --
    `mandate.spec_hash is None or mandate.signature is None` -- which no
    other test reached. A LOCKED mandate can never have a null signature
    (the database's own `locked_has_hash` check constraint, migrations/
    versions/0001_core.py, enforces this at the schema level -- signature
    is the one field a *LOCKED* mandate is guaranteed to have); the
    constraint names LOCKED specifically, so EXECUTING is the one status
    G1 accepts that can still legitimately reach this check unsigned."""
    clock = SystemClock()
    mandate = make_locked_mandate()
    unsigned = mandate.model_copy(update={"signature": None})
    assert compute_spec_hash(unsigned) == unsigned.spec_hash  # content, hence spec_hash, unchanged
    await seed_mandate(session_factory, unsigned, status=MandateStatus.EXECUTING)
    intent_hash = fake_hash(new_id("intent"))
    decision_id = await seed_decision(
        session_factory, clock, mandate=unsigned, intent_hash=intent_hash, verdict="ALLOW"
    )
    quote_id = await seed_quote(session_factory, clock, mandate_id=unsigned.mandate_id)
    fixture = GateFixture(unsigned, decision_id, quote_id, intent_hash, 840000)
    provider, breaker = _provider_and_breaker(clock)

    result = await execute_money_action(_req(fixture), session_factory, provider, clock, breaker)

    assert result.verdict == "DENY"
    assert result.reason_code == ReasonCode.MANDATE_UNSIGNED


async def test_gate_g1_rejects_expired_mandate(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    fixture = await seed_valid_gate_fixture(session_factory, clock)
    provider, breaker = _provider_and_breaker(clock)
    # a clock far past the mandate's expiry -- FrozenClock isn't needed
    # here since G1 only ever compares against mandate.temporal.expires_at,
    # which make_locked_mandate() fixes at 2027-01-01.
    from actl.platform.clock import FrozenClock

    far_future = FrozenClock(at=fixture.mandate.temporal.expires_at + timedelta(days=1))

    result = await execute_money_action(
        _req(fixture), session_factory, provider, far_future, breaker
    )

    assert result.verdict == "DENY"
    assert result.reason_code == ReasonCode.MANDATE_EXPIRED


# ---------------------------------------------------------------------------
# G2 -- intent binding + decision freshness
# ---------------------------------------------------------------------------


async def test_gate_g2_rejects_decision_for_other_intent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    fixture = await seed_valid_gate_fixture(session_factory, clock)
    provider, breaker = _provider_and_breaker(clock)
    req = replace(_req(fixture), intent_hash=fake_hash("a-different-intent"))

    result = await execute_money_action(req, session_factory, provider, clock, breaker)

    assert result.verdict == "DENY"
    assert result.reason_code == ReasonCode.INTENT_MISMATCH


async def test_gate_g2_rejects_unknown_decision(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    fixture = await seed_valid_gate_fixture(session_factory, clock)
    provider, breaker = _provider_and_breaker(clock)
    req = replace(_req(fixture), decision_id="dec_does_not_exist")

    result = await execute_money_action(req, session_factory, provider, clock, breaker)

    assert result.verdict == "DENY"
    assert result.reason_code == ReasonCode.INTENT_MISMATCH


async def test_gate_g2_rejects_a_decision_bound_to_a_different_mandate_spec_hash(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """§28 P10 release-readiness correction: `decision.mandate_spec_hash
    != mandate.spec_hash` -- decision drift, e.g. the mandate was
    re-signed after this decision was evaluated -- is a *different*
    INTENT_MISMATCH trigger than `test_gate_g2_rejects_decision_for_other_
    intent`'s mismatched `intent_hash`; both share the same reason code
    but are reached by different conditions."""
    clock = SystemClock()
    mandate = make_locked_mandate()
    await seed_mandate(session_factory, mandate)
    intent_hash = fake_hash(new_id("intent"))
    decision_id = new_id("dec")
    async with UnitOfWork(session_factory) as uow:
        await uow.decisions.add(
            DecisionRecord(
                decision_id=decision_id,
                engine_version="v1",
                mandate_id=mandate.mandate_id,
                mandate_spec_hash=fake_hash("a-different-mandate-spec"),
                intent_hash=intent_hash,
                verdict="ALLOW",
                reason_codes=[],
                rule_trace=[],
                evaluated_at=clock.now(),
                ttl_s=30,
                inputs_digest=fake_hash(decision_id),
            )
        )
        await uow.commit()
    quote_id = await seed_quote(session_factory, clock, mandate_id=mandate.mandate_id)
    fixture = GateFixture(mandate, decision_id, quote_id, intent_hash, 840000)
    provider, breaker = _provider_and_breaker(clock)

    result = await execute_money_action(_req(fixture), session_factory, provider, clock, breaker)

    assert result.verdict == "DENY"
    assert result.reason_code == ReasonCode.INTENT_MISMATCH


async def test_gate_g2_rejects_a_stale_decision(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    mandate = make_locked_mandate()
    await seed_mandate(session_factory, mandate)
    intent_hash = fake_hash(new_id("intent"))
    decision_id = await seed_decision(
        session_factory,
        clock,
        mandate=mandate,
        intent_hash=intent_hash,
        verdict="ALLOW",
        evaluated_at=clock.now() - timedelta(seconds=120),
        ttl_s=30,
    )
    quote_id = await seed_quote(session_factory, clock, mandate_id=mandate.mandate_id)
    fixture = GateFixture(mandate, decision_id, quote_id, intent_hash, 840000)
    provider, breaker = _provider_and_breaker(clock)

    result = await execute_money_action(_req(fixture), session_factory, provider, clock, breaker)

    assert result.verdict == "DENY"
    assert result.reason_code == ReasonCode.DECISION_STALE


# ---------------------------------------------------------------------------
# G3 -- policy verdict
# ---------------------------------------------------------------------------


async def test_gate_g3_rejects_a_deny_verdict_with_its_specific_reason(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    mandate = make_locked_mandate()
    await seed_mandate(session_factory, mandate)
    intent_hash = fake_hash(new_id("intent"))
    decision_id = await seed_decision(
        session_factory,
        clock,
        mandate=mandate,
        intent_hash=intent_hash,
        verdict="DENY",
        reason_codes=[ReasonCode.CATEGORY_NOT_ALLOWED],
    )
    quote_id = await seed_quote(session_factory, clock, mandate_id=mandate.mandate_id)
    fixture = GateFixture(mandate, decision_id, quote_id, intent_hash, 840000)
    provider, breaker = _provider_and_breaker(clock)

    result = await execute_money_action(_req(fixture), session_factory, provider, clock, breaker)

    assert result.verdict == "DENY"
    assert result.reason_code == ReasonCode.CATEGORY_NOT_ALLOWED


# ---------------------------------------------------------------------------
# G4 -- budget reservation
# ---------------------------------------------------------------------------


async def test_gate_g4_rejects_over_the_mandate_cap(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    fixture = await seed_valid_gate_fixture(session_factory, clock)  # cap is 900000
    provider, breaker = _provider_and_breaker(clock)
    req = _req(fixture, amount_minor=fixture.mandate.bounds.max_total_minor + 1)

    result = await execute_money_action(req, session_factory, provider, clock, breaker)

    assert result.verdict == "DENY"
    assert result.reason_code == ReasonCode.BUDGET_EXCEEDED
    assert await _reserved_balance(session_factory, fixture.mandate.mandate_id) == 0


# ---------------------------------------------------------------------------
# G5 -- freshness
# ---------------------------------------------------------------------------


async def test_gate_g5_rejects_an_expired_quote(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    mandate = make_locked_mandate()
    await seed_mandate(session_factory, mandate)
    intent_hash = fake_hash(new_id("intent"))
    decision_id = await seed_decision(
        session_factory, clock, mandate=mandate, intent_hash=intent_hash, verdict="ALLOW"
    )
    quote_id = await seed_quote(
        session_factory, clock, mandate_id=mandate.mandate_id, expires_in_s=-5
    )
    fixture = GateFixture(mandate, decision_id, quote_id, intent_hash, 840000)
    provider, breaker = _provider_and_breaker(clock)

    result = await execute_money_action(_req(fixture), session_factory, provider, clock, breaker)

    assert result.verdict == "DENY"
    assert result.reason_code == ReasonCode.QUOTE_EXPIRED
    assert (
        await _reserved_balance(session_factory, mandate.mandate_id) == 0
    )  # rolled back, not leaked


async def test_gate_g5_rejects_a_stale_catalog_version(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    mandate = make_locked_mandate()
    await seed_mandate(session_factory, mandate)
    intent_hash = fake_hash(new_id("intent"))
    decision_id = await seed_decision(
        session_factory, clock, mandate=mandate, intent_hash=intent_hash, verdict="ALLOW"
    )
    async with UnitOfWork(session_factory) as uow:
        live_version = await uow.catalog.current_version()
    quote_id = await seed_quote(
        session_factory, clock, mandate_id=mandate.mandate_id, catalog_version=live_version - 1000
    )
    fixture = GateFixture(mandate, decision_id, quote_id, intent_hash, 840000)
    provider, breaker = _provider_and_breaker(clock)

    result = await execute_money_action(_req(fixture), session_factory, provider, clock, breaker)

    assert result.verdict == "DENY"
    assert result.reason_code == ReasonCode.STALE_PRICE
    assert await _reserved_balance(session_factory, mandate.mandate_id) == 0


# ---------------------------------------------------------------------------
# G6 -- idempotency (replay)
# ---------------------------------------------------------------------------


async def test_gate_g6_replay_returns_stored_result(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    fixture = await seed_valid_gate_fixture(session_factory, clock)
    provider, breaker = _provider_and_breaker(clock)
    req = _req(fixture)

    first = await execute_money_action(req, session_factory, provider, clock, breaker)
    second = await execute_money_action(req, session_factory, provider, clock, breaker)

    assert first.verdict == "ALLOW"
    assert first.duplicate is False
    assert second.verdict == "ALLOW"
    assert second.duplicate is True
    assert second.order_id == first.order_id
    assert second.provider_order_id == first.provider_order_id
    # never double-reserved by the replay
    assert (
        await _reserved_balance(session_factory, fixture.mandate.mandate_id) == fixture.amount_minor
    )


# ---------------------------------------------------------------------------
# G7 -- write-ahead audit
# ---------------------------------------------------------------------------


async def test_gate_g7_intent_written_before_provider_call(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    fixture = await seed_valid_gate_fixture(session_factory, clock)
    provider, breaker = _provider_and_breaker(clock)

    result = await execute_money_action(_req(fixture), session_factory, provider, clock, breaker)
    assert result.verdict == "ALLOW"

    async with UnitOfWork(session_factory) as uow:
        await uow.audit_log.acquire_chain_lock("actl.audit_log")
        tail = await uow.audit_log.get_tail()
        assert tail is not None
        entries = await uow.audit_log.list_range(1, tail[0])
    budget_reserved = next(
        e
        for e in entries
        if e.action == "budget.reserved"
        and e.subject.get("mandate_id") == fixture.mandate.mandate_id
    )
    payment_intent = next(
        e
        for e in entries
        if e.action == "payment.intent" and e.payload.get("order_id") == result.order_id
    )
    assert budget_reserved.seq is not None
    assert payment_intent.seq is not None
    # G4's reservation audit precedes G7's write-ahead audit
    assert budget_reserved.seq < payment_intent.seq


# ---------------------------------------------------------------------------
# Gate order (§11 DESIGN RULE)
# ---------------------------------------------------------------------------


async def test_gate_order_g1_before_g2(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """An unknown mandate AND an unknown decision both hold -- G1 fires
    first, never G2."""
    clock = SystemClock()
    provider, breaker = _provider_and_breaker(clock)
    req = MoneyActionRequest(
        trace_id=new_id("trc"),
        mandate_id="mdt_does_not_exist",
        decision_id="dec_does_not_exist",
        quote_id="qte_does_not_exist",
        intent_hash=fake_hash("x"),
        amount_minor=1000,
        currency="INR",
        attempt_no=1,
    )

    result = await execute_money_action(req, session_factory, provider, clock, breaker)

    assert result.reason_code == ReasonCode.MANDATE_INVALID


async def test_gate_order_g4_before_g5(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Over budget AND an expired quote both hold -- G4 (budget) fires
    first, never G5 (freshness)."""
    clock = SystemClock()
    mandate = make_locked_mandate()
    await seed_mandate(session_factory, mandate)
    intent_hash = fake_hash(new_id("intent"))
    decision_id = await seed_decision(
        session_factory, clock, mandate=mandate, intent_hash=intent_hash, verdict="ALLOW"
    )
    quote_id = await seed_quote(
        session_factory, clock, mandate_id=mandate.mandate_id, expires_in_s=-5
    )
    fixture = GateFixture(
        mandate, decision_id, quote_id, intent_hash, mandate.bounds.max_total_minor + 1
    )
    provider, breaker = _provider_and_breaker(clock)

    result = await execute_money_action(_req(fixture), session_factory, provider, clock, breaker)

    assert result.reason_code == ReasonCode.BUDGET_EXCEEDED


# ---------------------------------------------------------------------------
# Typed result / no exception escapes (§28 P6 instruction 3)
# ---------------------------------------------------------------------------


async def test_gate_never_raises_on_malformed_input(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    provider, breaker = _provider_and_breaker(clock)
    req = MoneyActionRequest(
        trace_id=new_id("trc"),
        mandate_id="mdt_x",
        decision_id="dec_x",
        quote_id="qte_x",
        intent_hash="",
        amount_minor=0,
        currency="INR",
        attempt_no=1,
    )

    result = await execute_money_action(req, session_factory, provider, clock, breaker)

    assert result.verdict == "DENY"
    assert result.reason_code == ReasonCode.MALFORMED_REQUEST


async def test_gate_never_raises_on_unexpected_internal_failure(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()
    fixture = await seed_valid_gate_fixture(session_factory, clock)
    provider, breaker = _provider_and_breaker(clock)

    def _broken_session_factory() -> AsyncSession:
        raise RuntimeError("simulated internal failure -- e.g. a connection pool exhaustion")

    result = await execute_money_action(
        _req(fixture),
        _broken_session_factory,
        provider,
        clock,
        breaker,  # type: ignore[arg-type]
    )

    assert result.verdict == "DENY"
    assert result.reason_code == ReasonCode.INTERNAL_ERROR


async def test_gate_never_raises_when_g1_g5_itself_fails_unexpectedly(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """§28 P10 release-readiness correction: the test above breaks the
    halt-check's own try/except (`execute_money_action`'s very first
    UnitOfWork); this is the *other*, textually-later except-Exception --
    around `retry_with_full_jitter(lambda: _attempt_g1_through_g5(...))`
    -- reached only once the halt-check and malformed-input guard have
    already passed. Nothing durable happens on this path (G1-G5 never
    got to run), so there is no reservation to have leaked."""
    import actl.application.gate as gate_module

    clock = SystemClock()
    fixture = await seed_valid_gate_fixture(session_factory, clock)
    provider, breaker = _provider_and_breaker(clock)

    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated unexpected G1-G5 failure -- not a DBAPIError, never retried")

    monkeypatch.setattr(gate_module, "_attempt_g1_through_g5", _boom)

    result = await execute_money_action(_req(fixture), session_factory, provider, clock, breaker)

    assert result.verdict == "DENY"
    assert result.reason_code == ReasonCode.INTERNAL_ERROR
    assert await _reserved_balance(session_factory, fixture.mandate.mandate_id) == 0


# ---------------------------------------------------------------------------
# Compensation (C1) on a post-reservation provider failure
# ---------------------------------------------------------------------------


async def test_compensation_releases_reservation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """S2 (order creation) fails terminally after S1 (reservation)
    already committed -- the gate self-compensates: reservation released,
    mandate COMPENSATED, nothing left dangling from one
    execute_money_action call."""
    clock = SystemClock()
    fixture = await seed_valid_gate_fixture(session_factory, clock)
    provider = SimulatorAdapter(
        clock=clock, scenario=Scenario.TRANSIENT_FAILURE, fail_before_success=999
    )
    breaker = CircuitBreaker(name="razorpay", clock=clock, failure_threshold=999)

    result = await execute_money_action(_req(fixture), session_factory, provider, clock, breaker)

    assert result.verdict == "DENY"
    assert result.reason_code == ReasonCode.PROVIDER_TRANSIENT
    assert await _reserved_balance(session_factory, fixture.mandate.mandate_id) == 0

    async with UnitOfWork(session_factory) as uow:
        entries = await uow.ledger_entries.list_for_ref_id(_idempotency_key_of(fixture))
    assert _state_of(entries) == ReservationState.RELEASED

    async with UnitOfWork(session_factory) as uow:
        current = await uow.mandates.get(fixture.mandate.mandate_id)
    assert current is not None
    assert current[1] == MandateStatus.COMPENSATED


def _idempotency_key_of(fixture: GateFixture) -> str:
    from actl.application.payment_service import compute_idempotency_key

    return compute_idempotency_key(fixture.mandate.mandate_id, fixture.intent_hash, 1)
