"""Agent identity repository (§14.1, §28 P7 -- `agent_identities`). No P1
domain model exists for an identity; `AgentIdentityRecord` is a local,
infrastructure-only record, same precedent as `OrderRecord`/`SagaRecord`
before their owning phase.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from actl.infrastructure.db.models import AgentIdentityRow


@dataclass(frozen=True)
class AgentIdentityRecord:
    agent_id: str
    key_id: str
    alg: str
    not_before: datetime
    expires_at: datetime
    status: str = "ACTIVE"
    public_key_hex: str | None = None
    hmac_secret: str | None = None
    revoked_at: datetime | None = None


def _to_record(row: AgentIdentityRow) -> AgentIdentityRecord:
    return AgentIdentityRecord(
        agent_id=row.agent_id,
        key_id=row.key_id,
        alg=row.alg,
        public_key_hex=row.public_key_hex,
        hmac_secret=row.hmac_secret,
        status=row.status,
        not_before=row.not_before,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
    )


class AgentIdentityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, identity: AgentIdentityRecord) -> None:
        self._session.add(
            AgentIdentityRow(
                agent_id=identity.agent_id,
                key_id=identity.key_id,
                alg=identity.alg,
                public_key_hex=identity.public_key_hex,
                hmac_secret=identity.hmac_secret,
                status=identity.status,
                not_before=identity.not_before,
                expires_at=identity.expires_at,
                revoked_at=identity.revoked_at,
            )
        )

    async def get_by_key_id(self, key_id: str) -> AgentIdentityRecord | None:
        result = await self._session.execute(
            select(AgentIdentityRow).where(AgentIdentityRow.key_id == key_id)
        )
        row = result.scalar_one_or_none()
        return _to_record(row) if row is not None else None

    async def get_by_agent_id(self, agent_id: str) -> AgentIdentityRecord | None:
        row = await self._session.get(AgentIdentityRow, agent_id)
        return _to_record(row) if row is not None else None

    async def revoke(self, agent_id: str, *, revoked_at: datetime) -> None:
        row = await self._session.get(AgentIdentityRow, agent_id)
        if row is None:
            raise KeyError(agent_id)
        row.status = "REVOKED"
        row.revoked_at = revoked_at
