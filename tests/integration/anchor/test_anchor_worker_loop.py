"""§28 P11 instruction 4: "enqueue required anchor work through the
existing outbox/worker flow" -- proves worker._anchor_tick's real
outbox-poll-and-mark cycle against real Postgres (audit_checkpoints IS
the outbox here: every row with anchor_status='unanchored' is a unit of
enqueued work). A hand-written fake anchor client stands in for
MonadAnchor (§28 P11 instruction 4's own "use fakes for deterministic
offline coverage where a real chain is not needed" -- the real chain is
covered separately in test_monad_testnet_anvil.py).

Shares the session-scoped Postgres testcontainer with every other
tests/integration/* file (tests/integration/conftest.py) -- other tests
may leave their own unanchored checkpoints lying around (real
`AUDIT_CHECKPOINT_EVERY` boundaries crossed incidentally), so the fake
client below tolerates and harmlessly resolves any checkpoint it wasn't
explicitly told about, and every assertion here checks only the specific
checkpoint this test itself created.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl import config, worker
from actl.application.audit_service import append_entry
from actl.domain.audit.events import AuditAction
from actl.infrastructure.anchor.monad_testnet import AnchorConflictError, AnchorSubmission
from actl.infrastructure.db.uow import UnitOfWork
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import SystemClock
from actl.platform.ids import new_id

pytestmark = pytest.mark.asyncio(loop_scope="session")

CHECKPOINT_EVERY = 4


@pytest.fixture
def small_checkpoint_interval(monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr(config.settings, "audit_checkpoint_every", CHECKPOINT_EVERY)
    return CHECKPOINT_EVERY


async def _append_one(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with UnitOfWork(session_factory) as uow:
        await append_entry(
            uow,
            trace_id=new_id("trc"),
            actor_type="system",
            actor_id="anchor_worker_loop_test",
            action=AuditAction.MANDATE_LOCKED,
            subject={},
            payload={"nonce": new_id("nonce")},
        )
        await uow.commit()


async def _align_to_next_boundary(
    session_factory: async_sessionmaker[AsyncSession], checkpoint_every: int
) -> int:
    async with UnitOfWork(session_factory) as uow:
        tail = await uow.audit_log.get_tail()
    seq = tail[0] if tail is not None else 0
    while seq % checkpoint_every != 0:
        await _append_one(session_factory)
        seq += 1
    return seq


class _FakeAnchorClient:
    """Resolves any checkpoint it wasn't explicitly told about with a
    harmless default success -- ambient checkpoints from other tests
    sharing this session's Postgres must never crash this test."""

    def __init__(self, outcomes: dict[tuple[int, int], object]) -> None:
        self._outcomes = outcomes
        self.calls: list[tuple[int, int]] = []

    async def anchor_checkpoint(
        self, *, start_seq: int, end_seq: int, merkle_root_hex: str
    ) -> AnchorSubmission:
        self.calls.append((start_seq, end_seq))
        outcome = self._outcomes.get((start_seq, end_seq))
        if outcome is None:
            return AnchorSubmission(
                chain_id=10143, contract_address="0xAMBIENT", already_anchored=True
            )
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, AnchorSubmission)
        return outcome


async def test_anchor_tick_marks_a_pending_checkpoint_anchored_on_success(
    session_factory: async_sessionmaker[AsyncSession], small_checkpoint_interval: int
) -> None:
    start = await _align_to_next_boundary(session_factory, small_checkpoint_interval)
    for _ in range(small_checkpoint_interval):
        await _append_one(session_factory)
    to_seq = start + small_checkpoint_interval

    async with UnitOfWork(session_factory) as uow:
        checkpoint = await uow.audit_checkpoints.get_by_to_seq(to_seq)
    assert checkpoint is not None
    assert checkpoint.anchor_status == "unanchored"

    submission = AnchorSubmission(
        chain_id=10143, contract_address="0xDEAD", already_anchored=False, tx_hash="0xreal"
    )
    client = _FakeAnchorClient({(checkpoint.from_seq, to_seq): submission})
    breaker = CircuitBreaker(name="test-anchor-tick", clock=SystemClock())

    await worker._anchor_tick(client, SystemClock(), breaker, session_factory)

    async with UnitOfWork(session_factory) as uow:
        after = await uow.audit_checkpoints.get_by_to_seq(to_seq)
    assert after is not None
    assert after.anchor_status == "anchored"
    assert after.anchor_tx == "0xreal"
    assert after.anchor_chain_id == 10143
    assert after.anchor_contract_address == "0xDEAD"
    assert after.anchored_at is not None

    # A second tick must not resubmit -- the row is no longer 'unanchored'.
    client.calls.clear()
    await worker._anchor_tick(client, SystemClock(), breaker, session_factory)
    assert (checkpoint.from_seq, to_seq) not in client.calls


async def test_anchor_tick_marks_conflict_permanently_never_retried(
    session_factory: async_sessionmaker[AsyncSession], small_checkpoint_interval: int
) -> None:
    start = await _align_to_next_boundary(session_factory, small_checkpoint_interval)
    for _ in range(small_checkpoint_interval):
        await _append_one(session_factory)
    to_seq = start + small_checkpoint_interval

    async with UnitOfWork(session_factory) as uow:
        checkpoint = await uow.audit_checkpoints.get_by_to_seq(to_seq)
    assert checkpoint is not None

    client = _FakeAnchorClient(
        {(checkpoint.from_seq, to_seq): AnchorConflictError("on-chain root disagrees")}
    )
    breaker = CircuitBreaker(name="test-anchor-conflict", clock=SystemClock())

    await worker._anchor_tick(client, SystemClock(), breaker, session_factory)

    async with UnitOfWork(session_factory) as uow:
        after = await uow.audit_checkpoints.get_by_to_seq(to_seq)
    assert after is not None
    assert after.anchor_status == "conflict"
    assert after.anchor_last_error is not None
    assert after.anchor_tx is None

    # A conflict must never be retried -- excluded from future polls.
    async with UnitOfWork(session_factory) as uow:
        pending = await uow.audit_checkpoints.list_unanchored()
    assert to_seq not in {c.to_seq for c in pending}


async def test_anchor_tick_records_transient_failure_and_leaves_row_retryable(
    session_factory: async_sessionmaker[AsyncSession],
    small_checkpoint_interval: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config.settings, "max_retry_attempts", 1)
    start = await _align_to_next_boundary(session_factory, small_checkpoint_interval)
    for _ in range(small_checkpoint_interval):
        await _append_one(session_factory)
    to_seq = start + small_checkpoint_interval

    async with UnitOfWork(session_factory) as uow:
        checkpoint = await uow.audit_checkpoints.get_by_to_seq(to_seq)
    assert checkpoint is not None

    from actl.infrastructure.anchor.monad_testnet import TransientAnchorError

    client = _FakeAnchorClient(
        {(checkpoint.from_seq, to_seq): TransientAnchorError("rpc timeout")}
    )
    breaker = CircuitBreaker(name="test-anchor-transient", clock=SystemClock())

    await worker._anchor_tick(client, SystemClock(), breaker, session_factory)

    async with UnitOfWork(session_factory) as uow:
        after = await uow.audit_checkpoints.get_by_to_seq(to_seq)
    assert after is not None
    assert after.anchor_status == "unanchored"  # stays retryable
    assert after.anchor_attempts >= 1
    assert after.anchor_last_error is not None

    # Still present in the next poll -- the whole point of staying
    # 'unanchored' instead of a terminal state.
    async with UnitOfWork(session_factory) as uow:
        pending = await uow.audit_checkpoints.list_unanchored()
    assert to_seq in {c.to_seq for c in pending}
