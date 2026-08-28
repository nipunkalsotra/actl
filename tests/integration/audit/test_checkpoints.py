"""§16.1 Merkle checkpoints: a root computed and stored every
AUDIT_CHECKPOINT_EVERY entries. §28 P3 exit criteria:
test_merkle_root_stable_across_runs.

Uses a monkeypatched, small AUDIT_CHECKPOINT_EVERY so each test's boundary
is reachable in a handful of appends rather than the real default (64).
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl import config
from actl.application.audit_service import _write_checkpoint, append_entry, verify_chain
from actl.application.ports import Anchor
from actl.domain.audit.chain import parse_hex_prefixed
from actl.domain.audit.events import AuditAction
from actl.domain.audit.merkle import merkle_root
from actl.infrastructure.db.uow import UnitOfWork
from actl.platform.ids import new_id

pytestmark = pytest.mark.asyncio(loop_scope="session")

CHECKPOINT_EVERY = 4


@pytest.fixture
def small_checkpoint_interval(monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr(config.settings, "audit_checkpoint_every", CHECKPOINT_EVERY)
    return CHECKPOINT_EVERY


async def _append_one(
    session_factory: async_sessionmaker[AsyncSession], anchor: Anchor | None = None
) -> str:
    async with UnitOfWork(session_factory) as uow:
        record = await append_entry(
            uow,
            trace_id=new_id("trc"),
            actor_type="system",
            actor_id="checkpoint_test",
            action=AuditAction.MANDATE_LOCKED,
            subject={},
            payload={"nonce": new_id("nonce")},
            anchor=anchor,
        )
        await uow.commit()
    assert record.seq is not None
    return record.entry_hash


async def _align_to_next_boundary(
    session_factory: async_sessionmaker[AsyncSession], checkpoint_every: int
) -> int:
    """Append entries (if needed) until the tail seq is a multiple of
    checkpoint_every. Returns that aligned seq."""
    async with UnitOfWork(session_factory) as uow:
        tail = await uow.audit_log.get_tail()
    seq = tail[0] if tail is not None else 0
    while seq % checkpoint_every != 0:
        await _append_one(session_factory)
        seq += 1
    return seq


async def test_checkpoint_created_at_boundary_with_expected_root(
    session_factory: async_sessionmaker[AsyncSession], small_checkpoint_interval: int
) -> None:
    start = await _align_to_next_boundary(session_factory, small_checkpoint_interval)

    entry_hashes_hex = [
        await _append_one(session_factory) for _ in range(small_checkpoint_interval)
    ]
    to_seq = start + small_checkpoint_interval

    async with UnitOfWork(session_factory) as uow:
        checkpoint = await uow.audit_checkpoints.get_by_to_seq(to_seq)

    assert checkpoint is not None
    assert checkpoint.from_seq == start + 1
    assert checkpoint.to_seq == to_seq

    expected_root = merkle_root([parse_hex_prefixed(h) for h in entry_hashes_hex])
    assert checkpoint.merkle_root == f"sha256:{expected_root.hex()}"


async def test_merkle_root_stable_across_runs(
    session_factory: async_sessionmaker[AsyncSession], small_checkpoint_interval: int
) -> None:
    """The same segment's root, recomputed independently of the checkpoint
    that was actually stored, is byte-identical every time — determinism,
    not a one-off coincidence of a single run."""
    start = await _align_to_next_boundary(session_factory, small_checkpoint_interval)
    entry_hashes_hex = [
        await _append_one(session_factory) for _ in range(small_checkpoint_interval)
    ]
    to_seq = start + small_checkpoint_interval

    async with UnitOfWork(session_factory) as uow:
        checkpoint = await uow.audit_checkpoints.get_by_to_seq(to_seq)
    assert checkpoint is not None

    leaves = [parse_hex_prefixed(h) for h in entry_hashes_hex]
    root_a = merkle_root(leaves)
    root_b = merkle_root(leaves)
    root_c = merkle_root(list(leaves))  # a fresh list object, same contents

    assert root_a == root_b == root_c
    assert f"sha256:{root_a.hex()}" == checkpoint.merkle_root


async def test_checkpoint_is_idempotent_on_retry(
    session_factory: async_sessionmaker[AsyncSession], small_checkpoint_interval: int
) -> None:
    """A retried/duplicate checkpoint-write attempt for an already-recorded
    segment must not create a second, possibly-inconsistent row."""
    start = await _align_to_next_boundary(session_factory, small_checkpoint_interval)
    for _ in range(small_checkpoint_interval):
        await _append_one(session_factory)
    to_seq = start + small_checkpoint_interval

    async with UnitOfWork(session_factory) as uow:
        before = await uow.audit_checkpoints.list_all()
        before_count = sum(1 for c in before if c.to_seq == to_seq)
        assert before_count == 1

        # Simulate a retry/restart calling the checkpoint step again for
        # the same boundary — same transaction shape the append service
        # itself uses.
        await _write_checkpoint(uow, to_seq=to_seq)
        await uow.commit()

    async with UnitOfWork(session_factory) as uow:
        after = await uow.audit_checkpoints.list_all()
        after_count = sum(1 for c in after if c.to_seq == to_seq)

    assert after_count == 1


async def test_no_checkpoint_before_boundary_is_reached(
    session_factory: async_sessionmaker[AsyncSession], small_checkpoint_interval: int
) -> None:
    start = await _align_to_next_boundary(session_factory, small_checkpoint_interval)

    async with UnitOfWork(session_factory) as uow:
        checkpoints_before = {c.to_seq for c in await uow.audit_checkpoints.list_all()}

    # One short of the boundary: no new checkpoint should appear.
    for _ in range(small_checkpoint_interval - 1):
        await _append_one(session_factory)

    async with UnitOfWork(session_factory) as uow:
        checkpoints_after = {c.to_seq for c in await uow.audit_checkpoints.list_all()}

    assert checkpoints_after == checkpoints_before
    assert (start + small_checkpoint_interval) not in checkpoints_after


async def test_verify_chain_skips_checkpoints_whose_segment_is_not_fully_in_range(
    session_factory: async_sessionmaker[AsyncSession], small_checkpoint_interval: int
) -> None:
    """A checkpoint's segment can straddle the start of a partial verify
    range (e.g. verify-chain --from that lands mid-segment). Only
    checkpoints whose *entire* segment lies inside [from_seq, to_seq] may be
    checked — otherwise the recomputed root would be over the wrong,
    truncated set of entries and falsely report CHAIN BROKEN."""
    start = await _align_to_next_boundary(session_factory, small_checkpoint_interval)
    # Cross one full checkpoint boundary, then one entry into the next
    # segment — from_seq below lands strictly inside that second segment.
    for _ in range(small_checkpoint_interval + 1):
        await _append_one(session_factory)

    partial_from = start + small_checkpoint_interval + 1  # mid-segment
    partial_to = start + small_checkpoint_interval + 1

    async with UnitOfWork(session_factory) as uow:
        result = await verify_chain(uow, partial_from, partial_to)

    assert result.ok, result.break_
    assert result.checkpoints_matched == []


async def test_append_entry_calls_the_supplied_anchor_port_at_a_boundary(
    session_factory: async_sessionmaker[AsyncSession], small_checkpoint_interval: int
) -> None:
    """§16.1: "the root — and only the root — may be written." application
    depends only on the Anchor protocol (§28 P3 instruction 7) — a fake
    satisfying that protocol proves the call happens and only the root
    crosses it, without any real anchor adapter or network call."""

    class _RecordingAnchor:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def anchor_root(self, merkle_root: str) -> str | None:
            self.calls.append(merkle_root)
            return f"fake-tx:{len(self.calls)}"

    anchor: Anchor = _RecordingAnchor()
    start = await _align_to_next_boundary(session_factory, small_checkpoint_interval)
    for _ in range(small_checkpoint_interval):
        await _append_one(session_factory, anchor=anchor)
    to_seq = start + small_checkpoint_interval

    assert isinstance(anchor, _RecordingAnchor)
    assert len(anchor.calls) == 1

    async with UnitOfWork(session_factory) as uow:
        checkpoint = await uow.audit_checkpoints.get_by_to_seq(to_seq)
    assert checkpoint is not None
    assert checkpoint.merkle_root == anchor.calls[0]
    assert checkpoint.anchor_tx == "fake-tx:1"


async def test_append_entry_without_anchor_leaves_anchor_tx_unset(
    session_factory: async_sessionmaker[AsyncSession], small_checkpoint_interval: int
) -> None:
    start = await _align_to_next_boundary(session_factory, small_checkpoint_interval)
    for _ in range(small_checkpoint_interval):
        await _append_one(session_factory)  # no anchor supplied
    to_seq = start + small_checkpoint_interval

    async with UnitOfWork(session_factory) as uow:
        checkpoint = await uow.audit_checkpoints.get_by_to_seq(to_seq)

    assert checkpoint is not None
    assert checkpoint.anchor_tx is None
