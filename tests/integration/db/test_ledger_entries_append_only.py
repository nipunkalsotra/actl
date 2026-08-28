"""§12.1: "Append-only ledger_entries. Corrections are contra-entries;
rows are never updated or deleted." No carve-out — unlike audit_log there
is no narration-equivalent column, so every UPDATE and every DELETE is
rejected unconditionally. See docs/adr/0003-p2-persistence-decisions.md
decision 6."""

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
            "INSERT INTO ledger_entries (account, direction, amount_minor, ref_type, ref_id) "
            "VALUES (:account, 'credit', 100, 'reservation', :ref_id) "
            "RETURNING id"
        ),
        {"account": f"mandate:{new_id('mdt')}:available", "ref_id": new_id("rsv")},
    )
    await session.commit()
    return result.scalar_one()  # type: ignore[no-any-return]


async def test_update_is_rejected(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        entry_id = await _insert_seed_row(session)
        with pytest.raises(DBAPIError, match="append-only"):
            await session.execute(
                text("UPDATE ledger_entries SET amount_minor=200 WHERE id=:id"), {"id": entry_id}
            )
        await session.rollback()


async def test_delete_is_rejected(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        entry_id = await _insert_seed_row(session)
        with pytest.raises(DBAPIError, match="append-only"):
            await session.execute(
                text("DELETE FROM ledger_entries WHERE id=:id"), {"id": entry_id}
            )
        await session.rollback()


async def test_insert_still_succeeds(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """The trigger only guards UPDATE/DELETE — INSERT (the only legitimate
    way to record a ledger movement, including corrections as contra-entries
    per §12.1) is unaffected."""
    async with session_factory() as session:
        entry_id = await _insert_seed_row(session)
        assert entry_id > 0
