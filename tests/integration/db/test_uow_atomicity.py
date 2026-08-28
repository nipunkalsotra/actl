"""§28 P2 exit criteria — test names match the architecture doc exactly."""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.domain.mandate.state_machine import MandateStatus
from actl.infrastructure.db.models import AuditLogRow, MandateRow, OutboxRow
from actl.infrastructure.db.repositories.audit_log import AuditLogRecord
from actl.infrastructure.db.repositories.outbox import OutboxRecord
from actl.infrastructure.db.uow import UnitOfWork
from actl.platform.ids import new_id

from .conftest import make_locked_mandate

pytestmark = pytest.mark.asyncio(loop_scope="session")


class _SimulatedFailure(Exception):
    pass


async def test_rollback_leaves_no_outbox_row(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    mandate = make_locked_mandate()
    aggregate_id = new_id("evt")

    with pytest.raises(_SimulatedFailure):
        async with UnitOfWork(session_factory) as uow:
            await uow.mandates.add(mandate, MandateStatus.LOCKED)
            await uow.outbox.add(
                OutboxRecord(
                    aggregate="mandate",
                    aggregate_id=aggregate_id,
                    event_type="mandate.locked",
                    payload={"mandate_id": mandate.mandate_id},
                )
            )
            raise _SimulatedFailure("failure before commit")

    async with session_factory() as session:
        outbox_result = await session.execute(
            select(OutboxRow).where(OutboxRow.aggregate_id == aggregate_id)
        )
        assert outbox_result.scalar_one_or_none() is None
        assert await session.get(MandateRow, mandate.mandate_id) is None


async def test_commit_writes_state_audit_and_event(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    mandate = make_locked_mandate()
    aggregate_id = new_id("evt")
    # Well-formed sha256:<64-hex> strings — not chain-verified (this test
    # only checks UoW atomicity), but must parse as real hashes: P3's
    # chain reader treats every audit_log row as a real link when it scans
    # the table for the current tail, and this row lands in the same
    # shared table other tests' chains build on.
    entry_hash = f"sha256:{hashlib.sha256(mandate.mandate_id.encode()).hexdigest()}"
    prev_hash = f"sha256:{hashlib.sha256(b'uow-atomicity-test-prev').hexdigest()}"
    payload_hash = f"sha256:{hashlib.sha256(b'uow-atomicity-test-payload').hexdigest()}"

    async with UnitOfWork(session_factory) as uow:
        await uow.mandates.add(mandate, MandateStatus.LOCKED)
        await uow.audit_log.add(
            AuditLogRecord(
                trace_id=new_id("trc"),
                actor_type="human",
                actor_id="usr_test",
                action="mandate.locked",
                subject={"mandate_id": mandate.mandate_id},
                payload={"spec_hash": mandate.spec_hash},
                payload_hash=payload_hash,
                prev_hash=prev_hash,
                entry_hash=entry_hash,
            )
        )
        await uow.outbox.add(
            OutboxRecord(
                aggregate="mandate",
                aggregate_id=aggregate_id,
                event_type="mandate.locked",
                payload={"mandate_id": mandate.mandate_id},
            )
        )
        await uow.commit()

    async with session_factory() as session:
        mandate_row = await session.get(MandateRow, mandate.mandate_id)
        assert mandate_row is not None
        assert mandate_row.status == "LOCKED"

        audit_result = await session.execute(
            select(AuditLogRow).where(AuditLogRow.entry_hash == entry_hash)
        )
        assert audit_result.scalar_one_or_none() is not None

        outbox_result = await session.execute(
            select(OutboxRow).where(OutboxRow.aggregate_id == aggregate_id)
        )
        assert outbox_result.scalar_one_or_none() is not None


async def test_explicit_rollback_leaves_no_state(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Same guarantee as above, via `uow.rollback()` instead of an exception."""
    mandate = make_locked_mandate()

    async with UnitOfWork(session_factory) as uow:
        await uow.mandates.add(mandate, MandateStatus.LOCKED)
        await uow.rollback()

    async with session_factory() as session:
        assert await session.get(MandateRow, mandate.mandate_id) is None
