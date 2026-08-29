"""Outbox repository (§18.2 `outbox` — transactional outbox, §19). No P1
domain model exists yet (the relay worker is P2's own deliverable, not
domain logic); `OutboxRecord` is a local, infrastructure-only record."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import BigInteger, cast, func, select
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

    async def count_by_event_type_and_arm(self, event_type: str, arm: str) -> int:
        """§22.2 growth instrumentation: every metric is `count(event_type)`
        for one experiment arm -- the outbox *is* the persisted session/
        order fact table this build derives growth numbers from, no
        separate materialized view table needed (§18.1's own event-stream
        precedent: "streams are re-derivable from the outbox")."""
        result = await self._session.execute(
            select(func.count())
            .select_from(OutboxRow)
            .where(OutboxRow.event_type == event_type, OutboxRow.payload["arm"].astext == arm)
        )
        return int(result.scalar_one())

    async def sum_order_total_minor(self, arm: str) -> int:
        """sum(order.total_minor) for `order.completed` events in one arm
        (§22.2's AOV numerator)."""
        result = await self._session.execute(
            select(
                func.coalesce(
                    func.sum(cast(OutboxRow.payload["total_minor"].astext, BigInteger)), 0
                )
            ).where(
                OutboxRow.event_type == "order.completed", OutboxRow.payload["arm"].astext == arm
            )
        )
        # asyncpg returns SUM(bigint) as a Python Decimal, not int -- forced
        # back to int here so callers (and JSON serialisation) get a plain
        # integer minor-units amount, never a stringified Decimal.
        return int(result.scalar_one())
