"""Audit log repository (§18.2 `audit_log` — append-only, DB-trigger
enforced). No P1 domain model exists yet (chain linking / hashing is P3's
`domain/audit/chain.py`); `AuditLogRecord` is a local, infrastructure-only
record. This repository never UPDATEs or DELETEs a row — the database's own
trigger (migrations/versions/0002_audit_outbox.py) rejects both regardless.

Chain-aware methods (`acquire_chain_lock`, `get_tail`, `add_at_seq`) exist
alongside the plain `add()` P2 already used: the append service
(application/audit_service.py) is the only caller meant to use them
together to hold §16.1's gapless-sequence + single-writer-serialisation
guarantees; `add()` stays available for anything that just needs to persist
one already-fully-formed entry (as P2's UoW atomicity test does).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select, text, update
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
    seq: int | None = field(default=None)
    ts: datetime | None = field(default=None)


def _to_record(row: AuditLogRow) -> AuditLogRecord:
    return AuditLogRecord(
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
        seq=row.seq,
        ts=row.ts,
    )


def _chain_lock_key(chain_id: str) -> int:
    """A stable bigint key for pg_advisory_xact_lock from an arbitrary chain
    id string. Signed 63-bit range: Postgres advisory lock keys are bigint."""
    digest = hashlib.sha256(chain_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFF_FFFF_FFFF_FFFF


class AuditLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, entry: AuditLogRecord) -> None:
        """Persist one already-fully-formed entry with no concurrency
        guarantee — safe only when the caller already knows nothing else is
        writing at the same time (e.g. a single-threaded test). Computes an
        explicit seq via MAX(seq)+1 rather than the column's BIGSERIAL
        default: the two must never be mixed, because an explicit-seq
        insert (`add_at_seq`, used by the properly-locked append service)
        never advances the underlying sequence object, so a later
        default-relying insert would collide with a seq that's already
        taken. Concurrent-safe appends must go through
        `application.audit_service.append_entry`, which holds the chain's
        advisory lock for the whole read-tail-then-insert sequence."""
        seq = await self._next_seq_unsafe()
        row = AuditLogRow(
            seq=seq,
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

    async def _next_seq_unsafe(self) -> int:
        # Flush first: a prior add() earlier in the same transaction is
        # only visible to this MAX(seq) scan once flushed, not merely
        # staged in the session's identity map.
        await self._session.flush()
        result = await self._session.execute(
            text("SELECT COALESCE(MAX(seq), 0) + 1 FROM audit_log")
        )
        return result.scalar_one()  # type: ignore[no-any-return]

    async def acquire_chain_lock(self, chain_id: str) -> None:
        """§16.1: "Appends take a Postgres advisory lock keyed by the chain
        id." Transaction-scoped (auto-released on commit/rollback) — every
        appender must call this *before* reading the tail, or two
        concurrent appends can read the same prev_hash and fork the chain.
        """
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": _chain_lock_key(chain_id)}
        )

    async def get_tail(self) -> tuple[int, str] | None:
        """(seq, entry_hash) of the last row, or None for an empty chain.
        Only meaningful while holding the chain's advisory lock."""
        result = await self._session.execute(
            select(AuditLogRow.seq, AuditLogRow.entry_hash)
            .order_by(AuditLogRow.seq.desc())
            .limit(1)
        )
        row = result.first()
        return (row.seq, row.entry_hash) if row is not None else None

    async def add_at_seq(self, seq: int, entry: AuditLogRecord) -> None:
        """Insert with an explicitly-computed seq (§16.1: "seq is assigned
        inside the same transaction as the append") rather than relying on
        the seq column's BIGSERIAL default. A BIGSERIAL sequence advances
        even when its owning transaction rolls back, which would let a
        failed-then-retried append skip a number — a real gap indistinguishable
        from tampering, on every single retry. Explicit assignment from
        get_tail() inside the advisory-locked transaction has no such gap:
        if the transaction rolls back, no number was ever consumed."""
        row = AuditLogRow(
            seq=seq,
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
        return [_to_record(row) for row in result.scalars()]

    async def list_range(self, from_seq: int, to_seq: int) -> list[AuditLogRecord]:
        result = await self._session.execute(
            select(AuditLogRow)
            .where(AuditLogRow.seq >= from_seq, AuditLogRow.seq <= to_seq)
            .order_by(AuditLogRow.seq)
        )
        return [_to_record(row) for row in result.scalars()]

    async def get_by_seq(self, seq: int) -> AuditLogRecord | None:
        row = await self._session.get(AuditLogRow, seq)
        return _to_record(row) if row is not None else None

    async def update_narration(self, seq: int, narration: str) -> None:
        """§28 P8 instruction 4 / U3: touches ONLY the `narration` column.
        The database's own append-only trigger (migrations/versions/
        0002_audit_outbox.py) carves out exactly this: an UPDATE is
        allowed through when narration is the *sole* changed column,
        checked by comparing the whole row minus narration -- and a
        SQLAlchemy Core `update()` naturally SETs only the column named in
        `.values(...)`, so `entry_hash`/`prev_hash`/`payload`/every other
        field is structurally untouched by this call, not just by
        convention."""
        await self._session.execute(
            update(AuditLogRow).where(AuditLogRow.seq == seq).values(narration=narration)
        )

    async def get_seq_range_for_order(self, order_id: str) -> tuple[int, int] | None:
        """§14 order.status / receipt.issue: "audit sequence range". Uses
        the `subject->>'order_id'` expression index §18.2 names
        (`ix_audit_log_subject_order_id`, already created by P2's own
        0002_audit_outbox migration)."""
        result = await self._session.execute(
            select(func.min(AuditLogRow.seq), func.max(AuditLogRow.seq)).where(
                AuditLogRow.subject["order_id"].astext == order_id
            )
        )
        row = result.one()
        if row[0] is None:
            return None
        return (row[0], row[1])
