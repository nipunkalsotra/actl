"""Audit checkpoint repository (§18.2 `audit_checkpoints`). No P1 domain
model exists yet; `AuditCheckpointRecord` is a local, infrastructure-only
record — same pattern as the other P2 repositories without a domain model
(ADR 0003 decision 3)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from actl.infrastructure.db.models import AuditCheckpointRow


@dataclass(frozen=True)
class AuditCheckpointRecord:
    from_seq: int
    to_seq: int
    merkle_root: str
    anchor_tx: str | None = None


class AuditCheckpointRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, checkpoint: AuditCheckpointRecord) -> None:
        row = AuditCheckpointRow(
            from_seq=checkpoint.from_seq,
            to_seq=checkpoint.to_seq,
            merkle_root=checkpoint.merkle_root,
            anchor_tx=checkpoint.anchor_tx,
        )
        self._session.add(row)

    async def get_by_to_seq(self, to_seq: int) -> AuditCheckpointRecord | None:
        """Idempotency check: has the checkpoint ending at `to_seq` already
        been written? Used by the append service to make retries safe."""
        result = await self._session.execute(
            select(AuditCheckpointRow).where(AuditCheckpointRow.to_seq == to_seq)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return AuditCheckpointRecord(
            from_seq=row.from_seq,
            to_seq=row.to_seq,
            merkle_root=row.merkle_root,
            anchor_tx=row.anchor_tx,
        )

    async def list_all(self) -> list[AuditCheckpointRecord]:
        result = await self._session.execute(
            select(AuditCheckpointRow).order_by(AuditCheckpointRow.to_seq)
        )
        return [
            AuditCheckpointRecord(
                from_seq=row.from_seq,
                to_seq=row.to_seq,
                merkle_root=row.merkle_root,
                anchor_tx=row.anchor_tx,
            )
            for row in result.scalars()
        ]
