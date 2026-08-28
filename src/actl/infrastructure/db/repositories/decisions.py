"""Decision repository (§18.2 `policy_decisions`). Maps to/from the pure P1
DecisionRecord model (actl.domain.policy.decision)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from actl.domain.policy.decision import DecisionRecord, RuleTraceEntry
from actl.domain.policy.reason_codes import ReasonCode
from actl.infrastructure.db.models import DecisionRow


class DecisionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, decision: DecisionRecord) -> None:
        row = DecisionRow(
            id=decision.decision_id,
            mandate_id=decision.mandate_id,
            mandate_spec_hash=decision.mandate_spec_hash,
            intent_hash=decision.intent_hash,
            verdict=decision.verdict,
            reason_codes=[str(code) for code in decision.reason_codes],
            rule_trace=[entry.model_dump(mode="json") for entry in decision.rule_trace],
            engine_version=decision.engine_version,
            inputs_digest=decision.inputs_digest,
            evaluated_at=decision.evaluated_at,
            ttl_s=decision.ttl_s,
        )
        self._session.add(row)

    async def get(self, decision_id: str) -> DecisionRecord | None:
        row = await self._session.get(DecisionRow, decision_id)
        if row is None:
            return None
        return DecisionRecord(
            schema="actl.decision/v1",
            decision_id=row.id,
            engine_version=row.engine_version,
            mandate_id=row.mandate_id,
            mandate_spec_hash=row.mandate_spec_hash,
            intent_hash=row.intent_hash,
            verdict=row.verdict,  # type: ignore[arg-type]
            reason_codes=[ReasonCode(code) for code in row.reason_codes],
            rule_trace=[RuleTraceEntry.model_validate(entry) for entry in row.rule_trace],
            inputs_digest=row.inputs_digest,
            evaluated_at=row.evaluated_at,
            ttl_s=row.ttl_s,
        )
