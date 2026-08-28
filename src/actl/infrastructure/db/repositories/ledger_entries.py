"""Ledger entry repository (§18.2 `ledger_entries` — append-only, double
entry). No P1 domain model exists yet (`domain/ledger/` is empty; owned by
P6). `LedgerEntryRecord` is a local, infrastructure-only record."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from actl.infrastructure.db.models import LedgerEntryRow


@dataclass(frozen=True)
class LedgerEntryRecord:
    account: str
    direction: str  # "debit" | "credit"
    amount_minor: int
    ref_type: str
    ref_id: str


class LedgerEntryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, entry: LedgerEntryRecord) -> None:
        row = LedgerEntryRow(
            account=entry.account,
            direction=entry.direction,
            amount_minor=entry.amount_minor,
            ref_type=entry.ref_type,
            ref_id=entry.ref_id,
        )
        self._session.add(row)

    async def list_for_account(self, account: str) -> list[LedgerEntryRecord]:
        result = await self._session.execute(
            select(LedgerEntryRow).where(LedgerEntryRow.account == account)
        )
        return [
            LedgerEntryRecord(
                account=row.account,
                direction=row.direction,
                amount_minor=row.amount_minor,
                ref_type=row.ref_type,
                ref_id=row.ref_id,
            )
            for row in result.scalars()
        ]
