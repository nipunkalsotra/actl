"""Outbox repository (§18.2 `outbox` — transactional outbox, §19). No P1
domain model exists yet (the relay worker is P2's own deliverable, not
domain logic); `OutboxRecord` is a local, infrastructure-only record."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from actl.infrastructure.db.models import OutboxRow


@dataclass(frozen=True)
class OutboxRecord:
    aggregate: str
    aggregate_id: str
    event_type: str
    payload: dict[str, object]


class OutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, event: OutboxRecord) -> None:
        row = OutboxRow(
            aggregate=event.aggregate,
            aggregate_id=event.aggregate_id,
            event_type=event.event_type,
            payload=event.payload,
        )
        self._session.add(row)

    async def list_unpublished(self) -> list[OutboxRecord]:
        result = await self._session.execute(
            select(OutboxRow).where(OutboxRow.published_at.is_(None)).order_by(OutboxRow.id)
        )
        return [
            OutboxRecord(
                aggregate=row.aggregate,
                aggregate_id=row.aggregate_id,
                event_type=row.event_type,
                payload=row.payload,
            )
            for row in result.scalars()
        ]
