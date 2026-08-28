"""Webhook event repository (§18.2 `webhook_events` — duplicate absorption
via the unique constraint on provider_event_id, §20 F4). No P1 domain model
exists yet (the Razorpay webhook handler is P5's deliverable);
`WebhookEventRecord` is a local, infrastructure-only record."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from actl.infrastructure.db.models import WebhookEventRow


@dataclass(frozen=True)
class WebhookEventRecord:
    provider_event_id: str
    event_type: str
    signature_valid: bool
    payload: dict[str, object]


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

    async def get_by_provider_event_id(self, provider_event_id: str) -> WebhookEventRecord | None:
        result = await self._session.execute(
            select(WebhookEventRow).where(WebhookEventRow.provider_event_id == provider_event_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return WebhookEventRecord(
            provider_event_id=row.provider_event_id,
            event_type=row.event_type,
            signature_valid=row.signature_valid,
            payload=row.payload,
        )
