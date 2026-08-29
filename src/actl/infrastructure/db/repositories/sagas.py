"""Saga repository (§18.2-style, added P6 -- `sagas`). No P1 domain model
exists for a saga; `SagaRecord` is a local, infrastructure-only record,
same precedent as `OrderRecord`/`QuoteRecord` before their owning phase.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from actl.infrastructure.db.models import SagaRow


@dataclass(frozen=True)
class SagaRecord:
    id: str
    mandate_id: str
    decision_id: str
    quote_id: str
    amount_minor: int
    currency: str
    step: str
    status: str
    order_id: str | None = None


def _to_record(row: SagaRow) -> SagaRecord:
    return SagaRecord(
        id=row.id,
        mandate_id=row.mandate_id,
        decision_id=row.decision_id,
        quote_id=row.quote_id,
        amount_minor=row.amount_minor,
        currency=row.currency,
        step=row.step,
        status=row.status,
        order_id=row.order_id,
    )


class SagaRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, saga: SagaRecord, *, created_at: datetime) -> None:
        row = SagaRow(
            id=saga.id,
            mandate_id=saga.mandate_id,
            decision_id=saga.decision_id,
            quote_id=saga.quote_id,
            order_id=saga.order_id,
            amount_minor=saga.amount_minor,
            currency=saga.currency,
            step=saga.step,
            status=saga.status,
            created_at=created_at,
            updated_at=created_at,
        )
        self._session.add(row)

    async def get(self, saga_id: str) -> SagaRecord | None:
        row = await self._session.get(SagaRow, saga_id)
        return _to_record(row) if row is not None else None

    async def update(
        self,
        saga_id: str,
        *,
        step: str,
        status: str,
        updated_at: datetime,
        order_id: str | None = None,
    ) -> None:
        row = await self._session.get(SagaRow, saga_id)
        if row is None:
            raise KeyError(saga_id)
        row.step = step
        row.status = status
        row.updated_at = updated_at
        if order_id is not None:
            row.order_id = order_id
