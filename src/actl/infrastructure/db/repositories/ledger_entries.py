"""Ledger entry repository (§18.2 `ledger_entries` — append-only, double
entry). Pure ledger math (account naming, movement construction, balance
netting) lives in `domain.ledger.model`; row-locked reserve/capture/
release/sweep orchestration lives in `application.ledger_service` (§28
P6). `LedgerEntryRecord` is a local, infrastructure-only record."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

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
    created_at: datetime | None = None


class LedgerEntryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, entry: LedgerEntryRecord) -> None:
        """`created_at` is taken from the caller when supplied, never from
        the column's `server_default=func.now()` -- the sweeper compares it
        against an *injected* Clock (same reasoning as OrderRepository.add,
        §28 P5), and a DB-side wall-clock timestamp would silently desync
        from a FrozenClock in tests."""
        row = LedgerEntryRow(
            account=entry.account,
            direction=entry.direction,
            amount_minor=entry.amount_minor,
            ref_type=entry.ref_type,
            ref_id=entry.ref_id,
        )
        if entry.created_at is not None:
            row.created_at = entry.created_at
        self._session.add(row)

    async def list_for_account(self, account: str) -> list[LedgerEntryRecord]:
        result = await self._session.execute(
            select(LedgerEntryRow).where(LedgerEntryRow.account == account)
        )
        return [_to_record(row) for row in result.scalars()]

    async def list_for_ref_id(self, ref_id: str) -> list[LedgerEntryRecord]:
        """Every entry ever posted against one reservation (its original
        `reservation` pair, plus any later `capture`/`release`/`expire`
        pair) -- how `ledger_service` derives a reservation's current
        §12.2 state and how a balance is netted to zero."""
        result = await self._session.execute(
            select(LedgerEntryRow).where(LedgerEntryRow.ref_id == ref_id)
        )
        return [_to_record(row) for row in result.scalars()]

    async def list_reservations_older_than(self, cutoff: datetime) -> list[str]:
        """Distinct `ref_id`s of every `reservation`-type entry created
        before `cutoff` -- the sweeper's candidate set (§12.2); still-HELD
        ones among these get force-released."""
        result = await self._session.execute(
            select(LedgerEntryRow.ref_id)
            .where(LedgerEntryRow.ref_type == "reservation", LedgerEntryRow.created_at < cutoff)
            .distinct()
        )
        return list(result.scalars())


def _to_record(row: LedgerEntryRow) -> LedgerEntryRecord:
    return LedgerEntryRecord(
        account=row.account,
        direction=row.direction,
        amount_minor=row.amount_minor,
        ref_type=row.ref_type,
        ref_id=row.ref_id,
        created_at=row.created_at,
    )
