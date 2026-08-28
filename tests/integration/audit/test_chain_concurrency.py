"""§28 P3 exit criteria: test_chain_append_is_serialised_under_concurrency
(200 parallel appends, 0 forks). Named to sort first in this directory so it
reliably observes an empty chain — but every assertion is written to hold
regardless of what ran before it (see the start_seq handling below)."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application.audit_service import append_entry
from actl.domain.audit.chain import (
    GENESIS_PREV_HASH,
    compute_entry_hash,
    hex_prefixed,
    parse_hex_prefixed,
)
from actl.domain.audit.events import AuditAction
from actl.infrastructure.db.uow import UnitOfWork
from actl.platform.ids import new_id

pytestmark = pytest.mark.asyncio(loop_scope="session")

N = 200


async def _one_append(session_factory: async_sessionmaker[AsyncSession], i: int) -> None:
    async with UnitOfWork(session_factory) as uow:
        await append_entry(
            uow,
            trace_id=new_id("trc"),
            actor_type="system",
            actor_id="concurrency_test",
            action=AuditAction.MANDATE_LOCKED,
            subject={"i": i},
            payload={"i": i, "nonce": new_id("nonce")},
        )
        await uow.commit()


async def test_chain_append_is_serialised_under_concurrency(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with UnitOfWork(session_factory) as uow:
        tail = await uow.audit_log.get_tail()
    start_seq = tail[0] if tail is not None else 0

    await asyncio.gather(*(_one_append(session_factory, i) for i in range(N)))

    async with UnitOfWork(session_factory) as uow:
        rows = await uow.audit_log.list_range(start_seq + 1, start_seq + N)
        prior_entry = await uow.audit_log.get_by_seq(start_seq) if start_seq > 0 else None

    # (a) sequence numbers contiguous, no duplicates, no gaps
    assert len(rows) == N
    seqs = [row.seq for row in rows]
    assert seqs == list(range(start_seq + 1, start_seq + 1 + N))

    # (c) each entry's prev_hash equals the immediately previous entry_hash
    expected_prev = (
        hex_prefixed(GENESIS_PREV_HASH) if prior_entry is None else prior_entry.entry_hash
    )
    for row in rows:
        assert row.prev_hash == expected_prev
        expected_prev = row.entry_hash

    # (b) exactly one genesis entry exists
    if start_seq == 0:
        assert rows[0].prev_hash == hex_prefixed(GENESIS_PREV_HASH)
        assert all(row.prev_hash != hex_prefixed(GENESIS_PREV_HASH) for row in rows[1:])
    else:
        # genesis, if it exists at all, was claimed by whoever ran first;
        # this run must not have minted a second one
        assert all(row.prev_hash != hex_prefixed(GENESIS_PREV_HASH) for row in rows)

    # (d) recomputation verifies every hash
    for row in rows:
        recomputed = compute_entry_hash(parse_hex_prefixed(row.prev_hash), row.payload)
        assert hex_prefixed(recomputed) == row.entry_hash

    # no fork: 200 concurrent writers, 200 distinct seqs, 200 distinct
    # entry_hashes — two appends racing onto the same prev_hash would show
    # up here as a duplicate entry_hash or a seq collision (which the
    # UNIQUE seq primary key would itself have rejected as a 23505 error).
    assert len({row.entry_hash for row in rows}) == N
