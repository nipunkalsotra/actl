"""§20 F10 durable halt repository (§28 P9 production-readiness
correction, docs/adr/0010 decision 16). Single always-present row
(id='default'), same precedent as `CatalogRepository.current_version`/
`mutate_price`. No `clear()` method anywhere in this class, deliberately
-- see `application/integrity.py`'s own module docstring."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from actl.infrastructure.db.models import IntegrityHaltRow


@dataclass(frozen=True)
class HaltState:
    halted: bool
    reason: str | None
    tripped_at: datetime | None


class IntegrityHaltRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_state(self) -> HaltState:
        row = await self._session.get(IntegrityHaltRow, "default")
        if row is None:
            raise RuntimeError(
                "integrity_halt has no 'default' row -- migration 0007 not applied?"
            )
        return HaltState(halted=row.halted, reason=row.reason, tripped_at=row.tripped_at)

    async def trip(self, *, reason: str, tripped_seq: int | None, now: datetime) -> None:
        """Idempotent, first-trip-wins: only fires (and only ever will,
        until a human clears it directly in the database) while `halted`
        is still false, so the *original* incident's reason/timestamp is
        never overwritten by a later, possibly different failure found
        while already halted -- preserves the forensic record."""
        await self._session.execute(
            update(IntegrityHaltRow)
            .where(IntegrityHaltRow.id == "default", ~IntegrityHaltRow.halted)
            .values(halted=True, reason=reason, tripped_at=now, tripped_seq=tripped_seq)
        )
