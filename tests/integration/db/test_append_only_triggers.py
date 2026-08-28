"""§18.2 audit_log append-only trigger, tightened per the security review:
INSERT is always allowed; DELETE is always rejected; UPDATE is rejected
unless narration is the *sole* changed column — including when narration
changes alongside a protected column in the same statement, which the
original WHEN clause (`OLD.narration IS NOT DISTINCT FROM NEW.narration`)
did not catch. See docs/adr/0003-p2-persistence-decisions.md decision 6.

$ psql ... UPDATE audit_log SET payload=... -> ERROR: audit_log is append-only
$ psql ... DELETE FROM audit_log ...        -> ERROR: audit_log is append-only
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.platform.ids import new_id

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _insert_seed_row(session: AsyncSession) -> int:
    result = await session.execute(
        text(
            "INSERT INTO audit_log "
            "(trace_id, actor_type, actor_id, action, subject, payload, "
            " payload_hash, prev_hash, entry_hash) "
            "VALUES (:trace_id, 'system', 'sys', 'test.seed', '{}', '{}', "
            "        'sha256:a', 'sha256:0', :entry_hash) "
            "RETURNING seq"
        ),
        {"trace_id": new_id("trc"), "entry_hash": f"sha256:{new_id('trg')}"},
    )
    await session.commit()
    return result.scalar_one()  # type: ignore[no-any-return]


async def test_narration_only_update_is_allowed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """§18.2 WHY THIS WAY: the narration carve-out lets model-generated
    prose attach to an entry, without it ever becoming part of the
    cryptographic record."""
    async with session_factory() as session:
        seq = await _insert_seed_row(session)
        await session.execute(
            text("UPDATE audit_log SET narration='ai summary' WHERE seq=:seq"), {"seq": seq}
        )
        await session.commit()
        result = await session.execute(
            text("SELECT narration FROM audit_log WHERE seq=:seq"), {"seq": seq}
        )
        assert result.scalar_one() == "ai summary"


async def test_protected_field_update_is_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Any column other than narration is protected — payload here, but the
    trigger's row-minus-narration comparison covers every column equally."""
    async with session_factory() as session:
        seq = await _insert_seed_row(session)
        with pytest.raises(DBAPIError, match="append-only"):
            await session.execute(
                text("UPDATE audit_log SET payload=CAST(:payload AS JSONB) WHERE seq=:seq"),
                {"seq": seq, "payload": '{"x":1}'},
            )
        await session.rollback()


async def test_narration_plus_protected_field_update_is_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The gap closed by this correction: previously, changing narration
    *together with* payload in one statement slipped past the WHEN clause
    (narration differed old-vs-new, so the trigger never fired at all).
    The tightened trigger compares the whole row minus narration, so a
    payload change is caught regardless of what else the statement touches."""
    async with session_factory() as session:
        seq = await _insert_seed_row(session)
        with pytest.raises(DBAPIError, match="append-only"):
            await session.execute(
                text(
                    "UPDATE audit_log SET narration='second', payload=CAST(:payload AS JSONB) "
                    "WHERE seq=:seq"
                ),
                {"seq": seq, "payload": '{"x":1}'},
            )
        await session.rollback()


async def test_delete_is_rejected(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        seq = await _insert_seed_row(session)
        with pytest.raises(DBAPIError, match="append-only"):
            await session.execute(text("DELETE FROM audit_log WHERE seq=:seq"), {"seq": seq})
        await session.rollback()
