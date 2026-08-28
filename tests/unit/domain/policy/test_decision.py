from actl.domain.policy.decision import DecisionRecord, RuleTraceEntry


def test_decision_record_round_trips_schema_alias() -> None:
    record = DecisionRecord(
        schema="actl.decision/v1",
        decision_id="dec_1",
        engine_version="policy/1.0.0",
        mandate_id="mdt_1",
        mandate_spec_hash="sha256:a",
        intent_hash="sha256:b",
        verdict="ALLOW",
        reason_codes=["OK"],  # type: ignore[list-item]
        rule_trace=[RuleTraceEntry(rule="currency.match", input={}, result="pass")],
        evaluated_at="2026-08-28T09:04:11.220Z",  # type: ignore[arg-type]
        ttl_s=30,
        inputs_digest="sha256:c",
    )
    assert record.model_dump(by_alias=True)["schema"] == "actl.decision/v1"


def test_decision_record_is_immutable() -> None:
    import pytest
    from pydantic import ValidationError

    record = DecisionRecord(
        schema="actl.decision/v1",
        decision_id="dec_1",
        engine_version="policy/1.0.0",
        mandate_id="mdt_1",
        mandate_spec_hash="sha256:a",
        intent_hash="sha256:b",
        verdict="ALLOW",
        reason_codes=["OK"],  # type: ignore[list-item]
        rule_trace=[],
        evaluated_at="2026-08-28T09:04:11.220Z",  # type: ignore[arg-type]
        ttl_s=30,
        inputs_digest="sha256:c",
    )
    with pytest.raises(ValidationError):
        record.verdict = "DENY"  # type: ignore[misc]
