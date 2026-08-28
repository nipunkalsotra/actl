"""§28 P3 exit criteria: test_tamper_is_detected_at_exact_seq. Simulates the
same storage-layer tampering scripts/tamper.py performs (disable the
append-only trigger, mutate a committed row, re-enable it) and proves
verify_chain reports the exact seq, not just "something's wrong somewhere"."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from actl.application.audit_service import append_entry, verify_chain
from actl.domain.audit.events import AuditAction
from actl.infrastructure.db.uow import UnitOfWork
from actl.platform.ids import new_id

pytestmark = pytest.mark.asyncio(loop_scope="session")

SEGMENT_SIZE = 10
TAMPER_OFFSET = 5  # tamper the 5th of 10 appended entries


async def _append_one(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with UnitOfWork(session_factory) as uow:
        await append_entry(
            uow,
            trace_id=new_id("trc"),
            actor_type="system",
            actor_id="tamper_test",
            action=AuditAction.MANDATE_LOCKED,
            subject={},
            payload={"nonce": new_id("nonce")},
        )
        await uow.commit()


async def test_tamper_is_detected_at_exact_seq(
    engine: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with UnitOfWork(session_factory) as uow:
        tail = await uow.audit_log.get_tail()
    start_seq = tail[0] if tail is not None else 0

    for _ in range(SEGMENT_SIZE):
        await _append_one(session_factory)

    tamper_seq = start_seq + TAMPER_OFFSET

    # Same mechanism as scripts/tamper.py: bypass the append-only trigger
    # via a table-owner session, exactly what the trigger cannot stop and
    # what verify-chain exists to catch instead.
    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_update"))
        try:
            result = await conn.execute(
                text(
                    "UPDATE audit_log SET payload = payload || '{\"tampered\": true}'::jsonb "
                    "WHERE seq = :seq"
                ),
                {"seq": tamper_seq},
            )
            assert result.rowcount == 1
        finally:
            await conn.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_update"))

    async with UnitOfWork(session_factory) as uow:
        verification = await verify_chain(uow, start_seq + 1, start_seq + SEGMENT_SIZE)

    assert not verification.ok
    assert verification.break_ is not None
    assert verification.break_.seq == tamper_seq
    assert verification.break_.expected_entry_hash != verification.break_.computed_entry_hash
    # entries strictly before the tampered one verified intact
    assert verification.entries_verified == tamper_seq - (start_seq + 1)
