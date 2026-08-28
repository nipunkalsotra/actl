"""Webhook event repository (§18.2 `webhook_events` — duplicate absorption
via the unique constraint on provider_event_id, §20 F4). No P1 domain model
exists yet (the Razorpay webhook handler is P5's deliverable);
`WebhookEventRecord` is a local, infrastructure-only record."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from actl.infrastructure.db.models import WebhookEventRow


@dataclass(frozen=True)
class WebhookEventRecord:
    provider_event_id: str
    event_type: str
    signature_valid: bool
    payload: dict[str, object]
    processed_at: datetime | None = None


class WebhookEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, event: WebhookEventRecord) -> None:
        row = WebhookEventRow(
            provider_event_id=event.provider_event_id,
            event_type=event.event_type,
            signature_valid=event.signature_valid,
            payload=event.payload,
        )
        self._session.add(row)

    async def claim(self, event: WebhookEventRecord) -> bool:
        """§15.3 point 2: "duplicates are absorbed at the database, not
        application logic." `INSERT ... ON CONFLICT (provider_event_id) DO
        NOTHING` — returns True for a genuinely new delivery, False for a
        replay (whether a real Razorpay retry or a deliberately re-sent
        `actl replay-webhook`). Race-safe: two concurrent deliveries of the
        same event_id can never both win."""
        stmt = (
            pg_insert(WebhookEventRow)
            .values(
                provider_event_id=event.provider_event_id,
                event_type=event.event_type,
                signature_valid=event.signature_valid,
                payload=event.payload,
            )
            .on_conflict_do_nothing(index_elements=["provider_event_id"])
        )
        result = cast("CursorResult[Any]", await self._session.execute(stmt))
        return result.rowcount > 0

    async def get_by_provider_event_id(self, provider_event_id: str) -> WebhookEventRecord | None:
        result = await self._session.execute(
            select(WebhookEventRow).where(WebhookEventRow.provider_event_id == provider_event_id)
        )
        row = result.scalar_one_or_none()
        return _to_record(row) if row is not None else None

    async def list_unprocessed(self) -> list[WebhookEventRecord]:
        """§15.3 point 3: the worker's own queue — signature already
        verified true at claim time; a signature-invalid delivery is
        persisted for evidence (§15.3 point 1) but never processed."""
        result = await self._session.execute(
            select(WebhookEventRow)
            .where(
                WebhookEventRow.processed_at.is_(None),
                WebhookEventRow.signature_valid.is_(True),
            )
            .order_by(WebhookEventRow.id)
        )
        return [_to_record(row) for row in result.scalars()]

    async def mark_processed(self, provider_event_id: str, *, processed_at: datetime) -> None:
        result = await self._session.execute(
            select(WebhookEventRow).where(WebhookEventRow.provider_event_id == provider_event_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise KeyError(provider_event_id)
        row.processed_at = processed_at


def _to_record(row: WebhookEventRow) -> WebhookEventRecord:
    return WebhookEventRecord(
        provider_event_id=row.provider_event_id,
        event_type=row.event_type,
        signature_valid=row.signature_valid,
        payload=row.payload,
        processed_at=row.processed_at,
    )
