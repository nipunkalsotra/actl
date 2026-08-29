"""§28 P8 instruction 4 / exit criteria: U3 narration is written only to
audit_log.narration, excluded from payload_hash, and updating it never
invalidates `actl verify-chain` -- the exact required proof, against a
real Postgres container (P2/P3's append-only trigger is a real database
object, not something a fake/mock could prove anything about).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from actl.application.audit_service import append_entry, verify_chain
from actl.application.conversation.narration import narrate_and_store
from actl.domain.audit.events import AuditAction
from actl.infrastructure.db.uow import UnitOfWork
from tests.support.fake_llm_client import AlwaysFailsLLMClient, ScriptedLLMClient

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _append_one(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[int, str]:
    async with UnitOfWork(session_factory) as uow:
        entry = await append_entry(
            uow,
            trace_id="trc_narration_test",
            actor_type="agent",
            actor_id="agt_test",
            action=AuditAction.CATALOG_QUERIED,
            subject={"x": 1},
            payload={"y": 2},
        )
        await uow.commit()
    assert entry.seq is not None
    return entry.seq, entry.entry_hash


async def test_narration_excluded_from_payload_hash(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """§28 P8 exit criteria: test_narration_excluded_from_payload_hash."""
    seq, entry_hash_before = await _append_one(session_factory)

    llm = ScriptedLLMClient(text_responses=["The catalog was queried."])
    async with UnitOfWork(session_factory) as uow:
        entry = await uow.audit_log.get_by_seq(seq)
        assert entry is not None
        assert entry.narration is None
        wrote = await narrate_and_store(llm, uow, entry)
        await uow.commit()
    assert wrote is True

    async with UnitOfWork(session_factory) as uow:
        after = await uow.audit_log.get_by_seq(seq)
    assert after is not None
    assert after.narration == "The catalog was queried."
    # payload_hash/entry_hash are computed only from `payload` (§16.1) --
    # writing narration afterwards cannot have changed either.
    assert after.entry_hash == entry_hash_before


async def test_updating_narration_does_not_invalidate_verify_chain(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """§28 P8 instruction 4's required proof."""
    seq, _ = await _append_one(session_factory)

    async with UnitOfWork(session_factory) as uow:
        before = await verify_chain(uow, seq, seq)
    assert before.ok is True

    llm = ScriptedLLMClient(text_responses=["A plain-English narration."])
    async with UnitOfWork(session_factory) as uow:
        entry = await uow.audit_log.get_by_seq(seq)
        assert entry is not None
        assert await narrate_and_store(llm, uow, entry) is True
        await uow.commit()

    async with UnitOfWork(session_factory) as uow:
        after = await verify_chain(uow, seq, seq)
    assert after.ok is True
    assert after.head_entry_hash == before.head_entry_hash


async def test_narration_failure_leaves_the_entry_and_chain_untouched(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seq, entry_hash = await _append_one(session_factory)

    async with UnitOfWork(session_factory) as uow:
        entry = await uow.audit_log.get_by_seq(seq)
        assert entry is not None
        wrote = await narrate_and_store(AlwaysFailsLLMClient(), uow, entry)
        await uow.commit()
    assert wrote is False

    async with UnitOfWork(session_factory) as uow:
        after = await uow.audit_log.get_by_seq(seq)
        chain = await verify_chain(uow, seq, seq)
    assert after is not None
    assert after.narration is None
    assert after.entry_hash == entry_hash
    assert chain.ok is True


async def test_a_direct_update_to_any_other_column_is_still_rejected_by_the_trigger(
    engine: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The narration carve-out is narrow by construction: this proves the
    append-only trigger still rejects an UPDATE touching a column other
    than narration, even now that narration updates are routinely used."""
    seq, _ = await _append_one(session_factory)
    async with engine.begin() as conn:
        with pytest.raises(DBAPIError, match="append-only"):
            await conn.execute(
                text("UPDATE audit_log SET action = 'TAMPERED' WHERE seq = :seq"), {"seq": seq}
            )
