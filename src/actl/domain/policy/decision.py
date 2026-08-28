"""DecisionRecord (§8.2): explainability as an artefact. Replayable
byte-for-byte — same (mandate, intent, ctx) in, same rule_trace/verdict/
inputs_digest out."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from actl.domain.policy.reason_codes import ReasonCode


class RuleTraceEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule: str
    input: dict[str, object]
    result: Literal["pass", "fail"]


class DecisionRecord(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    schema_: Literal["actl.decision/v1"] = Field(alias="schema", default="actl.decision/v1")
    decision_id: str
    engine_version: str
    mandate_id: str
    mandate_spec_hash: str
    intent_hash: str
    verdict: Literal["ALLOW", "DENY"]
    reason_codes: list[ReasonCode]
    rule_trace: list[RuleTraceEntry]
    evaluated_at: datetime
    ttl_s: int
    inputs_digest: str
