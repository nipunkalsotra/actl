"""Idempotency key repository (§18.2 `idempotency_keys`, §19: deterministic
derivation from (mandate, intent, attempt), local claim + provider-side
key). No P1 domain model exists yet; `IdempotencyKeyRecord` is a local,
infrastructure-only record."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from actl.infrastructure.db.models import IdempotencyKeyRow


@dataclass(frozen=True)
class IdempotencyKeyRecord:
    key: str
    request_hash: str
    state: str  # "IN_FLIGHT" | "COMPLETED" | "FAILED"
    expires_at: datetime
    response: dict[str, object] | None = None


class IdempotencyKeyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, record: IdempotencyKeyRecord) -> None:
        row = IdempotencyKeyRow(
            key=record.key,
            request_hash=record.request_hash,
            state=record.state,
            response=record.response,
            expires_at=record.expires_at,
        )
        self._session.add(row)

    async def get(self, key: str) -> IdempotencyKeyRecord | None:
        row = await self._session.get(IdempotencyKeyRow, key)
        if row is None:
            return None
        return IdempotencyKeyRecord(
            key=row.key,
            request_hash=row.request_hash,
            state=row.state,
            expires_at=row.expires_at,
            response=row.response,
        )
