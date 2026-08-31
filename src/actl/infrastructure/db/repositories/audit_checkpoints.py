"""Audit checkpoint repository (§18.2 `audit_checkpoints`). No P1 domain
model exists yet; `AuditCheckpointRecord` is a local, infrastructure-only
record — same pattern as the other P2 repositories without a domain model
(ADR 0003 decision 3).

§28 P11 adds the anchor_* columns/methods: `list_unanchored`/`mark_
anchored`/`mark_conflict`/`record_attempt_failure` back the optional
Monad worker's outbox-style poll loop (`worker.py::_anchor_loop`),
`get_covering_seq` backs the explain-endpoint anchor lookup
(`application/explain_service.py`). None of this is read when
ANCHOR_PROVIDER=noop (default) -- every row just carries anchor_status=
'unanchored' forever, untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from actl.infrastructure.db.models import AuditCheckpointRow


@dataclass(frozen=True)
class AuditCheckpointRecord:
    from_seq: int
    to_seq: int
    merkle_root: str
    anchor_tx: str | None = None
    anchor_status: str = "unanchored"
    anchor_chain_id: int | None = None
    anchor_contract_address: str | None = None
    anchor_attempts: int = 0
    anchor_last_error: str | None = None
    anchored_at: datetime | None = None


def _to_record(row: AuditCheckpointRow) -> AuditCheckpointRecord:
    return AuditCheckpointRecord(
        from_seq=row.from_seq,
        to_seq=row.to_seq,
        merkle_root=row.merkle_root,
        anchor_tx=row.anchor_tx,
        anchor_status=row.anchor_status,
        anchor_chain_id=row.anchor_chain_id,
        anchor_contract_address=row.anchor_contract_address,
        anchor_attempts=row.anchor_attempts,
        anchor_last_error=row.anchor_last_error,
        anchored_at=row.anchored_at,
    )


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
        return None if row is None else _to_record(row)

    async def list_all(self) -> list[AuditCheckpointRecord]:
        result = await self._session.execute(
            select(AuditCheckpointRow).order_by(AuditCheckpointRow.to_seq)
        )
        return [_to_record(row) for row in result.scalars()]

    async def get_covering_seq(self, seq: int) -> AuditCheckpointRecord | None:
        """The checkpoint whose [from_seq, to_seq] range contains `seq`, if
        that segment has been checkpointed yet -- §28 P11 explain-endpoint
        anchor lookup. None if `seq` falls in the current, still-open
        (not yet a full AUDIT_CHECKPOINT_EVERY segment) tail."""
        result = await self._session.execute(
            select(AuditCheckpointRow)
            .where(AuditCheckpointRow.from_seq <= seq, AuditCheckpointRow.to_seq >= seq)
            .order_by(AuditCheckpointRow.to_seq)
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_record(row)

    async def list_unanchored(self) -> list[AuditCheckpointRecord]:
        """§28 P11 worker outbox poll: every checkpoint not yet anchored
        (or still retrying) -- 'conflict' rows are excluded, a permanent
        failure state that must never be retried automatically."""
        result = await self._session.execute(
            select(AuditCheckpointRow)
            .where(AuditCheckpointRow.anchor_status == "unanchored")
            .order_by(AuditCheckpointRow.to_seq)
        )
        return [_to_record(row) for row in result.scalars()]

    async def mark_anchored(
        self,
        to_seq: int,
        *,
        tx_hash: str | None,
        chain_id: int,
        contract_address: str,
        anchored_at: datetime,
    ) -> None:
        row = await self._get_row(to_seq)
        row.anchor_status = "anchored"
        row.anchor_tx = tx_hash
        row.anchor_chain_id = chain_id
        row.anchor_contract_address = contract_address
        row.anchored_at = anchored_at
        row.anchor_last_error = None

    async def mark_conflict(self, to_seq: int, *, error: str) -> None:
        """§28 P11 instruction 4: an on-chain root that disagrees with the
        local checkpoint is a permanent integrity failure -- never retried,
        left for a human via docs/runbook.md."""
        row = await self._get_row(to_seq)
        row.anchor_status = "conflict"
        row.anchor_last_error = error

    async def record_attempt_failure(self, to_seq: int, *, error: str) -> None:
        """Transient failure (RPC timeout, exhausted retries): stays
        'unanchored' so the next worker tick retries it."""
        row = await self._get_row(to_seq)
        row.anchor_attempts += 1
        row.anchor_last_error = error

    async def _get_row(self, to_seq: int) -> AuditCheckpointRow:
        result = await self._session.execute(
            select(AuditCheckpointRow).where(AuditCheckpointRow.to_seq == to_seq)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise ValueError(f"no checkpoint with to_seq={to_seq}")
        return row
