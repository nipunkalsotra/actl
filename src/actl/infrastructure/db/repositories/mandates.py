"""Mandate repository (§18.2 `mandates`). Maps to/from the pure P1 Mandate
model (actl.domain.mandate.models) — the domain layer never learns
SQLAlchemy exists; only this module does the translation.

`spec` stores the complete mandate JSON (§18.2 comment: "the full v1
object"), so loading a row is exactly `Mandate.model_validate(row.spec)` —
the other columns (spec_hash, signature, currency, caps, ...) are
denormalized extracts for indexing and the `locked_has_hash` constraint,
not a second source of truth. `status` lives outside the immutable Mandate
model by P1 design (ADR 0002 decision 3), so it's a separate parameter here.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from actl.domain.mandate.models import Mandate
from actl.domain.mandate.state_machine import MandateStatus
from actl.infrastructure.db.models import MandateRow


class MandateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, mandate: Mandate, status: MandateStatus) -> None:
        if mandate.spec_hash is None:
            raise ValueError("cannot persist a mandate with no spec_hash")
        row = MandateRow(
            id=mandate.mandate_id,
            version=mandate.version,
            status=status.value,
            principal_id=mandate.principal.id,
            delegate_id=mandate.delegate.id,
            spec=mandate.model_dump(mode="json", by_alias=True),
            spec_hash=mandate.spec_hash,
            signature=mandate.signature.value if mandate.signature else None,
            currency=mandate.bounds.currency,
            max_total_minor=mandate.bounds.max_total_minor,
            max_unit_minor=mandate.bounds.max_unit_minor,
            max_transactions=mandate.bounds.max_transactions,
            not_before=mandate.temporal.not_before,
            expires_at=mandate.temporal.expires_at,
        )
        self._session.add(row)

    async def get(self, mandate_id: str) -> tuple[Mandate, MandateStatus] | None:
        row = await self._session.get(MandateRow, mandate_id)
        if row is None:
            return None
        return Mandate.model_validate(row.spec), MandateStatus(row.status)

    async def get_created_at(self, mandate_id: str) -> datetime | None:
        """§28 P10 explain endpoint: the "mandate.locked" timeline fact's
        timestamp. `locked_at` exists as a column but no write path in this
        build ever sets it (mandate issuance is the buyer-agent's own
        system, out of this merchant-side build's scope) -- `created_at`
        (this merchant's own ingestion time, always populated) is the
        honest, always-available proxy."""
        row = await self._session.get(MandateRow, mandate_id)
        return row.created_at if row is not None else None

    async def update_status(self, mandate_id: str, status: MandateStatus) -> None:
        row = await self._session.get(MandateRow, mandate_id)
        if row is None:
            raise KeyError(mandate_id)
        row.status = status.value

    async def get_for_update(self, mandate_id: str) -> tuple[Mandate, MandateStatus] | None:
        """§12.1: `SELECT ... FOR UPDATE` on the mandate row -- the
        serialisation point that makes concurrent reservation attempts
        against the same mandate impossible to over-admit (§28 P6 gate G4).
        Row lock is released when the caller's transaction commits/rolls
        back, never explicitly here."""
        result = await self._session.execute(
            select(MandateRow).where(MandateRow.id == mandate_id).with_for_update()
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return Mandate.model_validate(row.spec), MandateStatus(row.status)
