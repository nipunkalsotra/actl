"""Audit log repository (§18.2 `audit_log` — append-only, DB-trigger
enforced). No P1 domain model exists yet (chain linking / hashing is P3's
`domain/audit/chain.py`); `AuditLogRecord` is a local, infrastructure-only
record. This repository only ever INSERTs — there is no update/delete
method, and the database's own trigger (migrations/versions/0002_audit_outbox.py)
rejects both regardless."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from actl.infrastructure.db.models import AuditLogRow


@dataclass(frozen=True)
class AuditLogRecord:
    trace_id: str
    actor_type: str
    actor_id: str
    action: str
    subject: dict[str, object]
    payload: dict[str, object]
    payload_hash: str
    prev_hash: str
    entry_hash: str
    narration: str | None = field(default=None)


class AuditLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, entry: AuditLogRecord) -> None:
        row = AuditLogRow(
            trace_id=entry.trace_id,
            actor_type=entry.actor_type,
            actor_id=entry.actor_id,
            action=entry.action,
            subject=entry.subject,
            payload=entry.payload,
            payload_hash=entry.payload_hash,
            prev_hash=entry.prev_hash,
            entry_hash=entry.entry_hash,
            narration=entry.narration,
        )
        self._session.add(row)

    async def get_by_trace_id(self, trace_id: str) -> list[AuditLogRecord]:
        result = await self._session.execute(
            select(AuditLogRow).where(AuditLogRow.trace_id == trace_id).order_by(AuditLogRow.seq)
        )
        return [
            AuditLogRecord(
                trace_id=row.trace_id,
                actor_type=row.actor_type,
                actor_id=row.actor_id,
                action=row.action,
                subject=row.subject,
                payload=row.payload,
                payload_hash=row.payload_hash,
                prev_hash=row.prev_hash,
                entry_hash=row.entry_hash,
                narration=row.narration,
            )
            for row in result.scalars()
        ]
