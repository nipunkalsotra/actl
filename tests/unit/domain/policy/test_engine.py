from actl.domain.policy.engine import ENGINE_VERSION, evaluate
from actl.domain.policy.reason_codes import ReasonCode

from .conftest import build_locked_mandate, build_matching_context, build_matching_intent


def test_evaluate_allows_a_valid_purchase() -> None:
    mandate = build_locked_mandate()
    intent = build_matching_intent(mandate)
    ctx = build_matching_context()
    decision = evaluate(mandate, intent, ctx)
    assert decision.verdict == "ALLOW"
    assert decision.reason_codes == [ReasonCode.OK]
    assert len(decision.rule_trace) == 12
    assert decision.engine_version == ENGINE_VERSION
    assert decision.decision_id == ctx.decision_id
    assert decision.mandate_id == mandate.mandate_id
    assert decision.mandate_spec_hash == mandate.spec_hash
    assert decision.intent_hash == intent.intent_hash
    assert decision.evaluated_at == ctx.now
    assert decision.ttl_s == ctx.decision_ttl_s


def test_evaluate_denies_and_collects_every_failing_reason() -> None:
    mandate = build_locked_mandate()
    intent = build_matching_intent(mandate, unit_price_minor=500000, refundable=False)
    ctx = build_matching_context()
    decision = evaluate(mandate, intent, ctx)
    assert decision.verdict == "DENY"
    assert ReasonCode.UNIT_CAP_EXCEEDED in decision.reason_codes
    assert ReasonCode.REFUND_POLICY_VIOLATION in decision.reason_codes
    assert ReasonCode.OK not in decision.reason_codes


def test_evaluate_runs_all_twelve_rules_even_after_first_failure() -> None:
    """§10.1: no short-circuit — every rule always runs."""
    mandate = build_locked_mandate()
    intent = build_matching_intent(mandate, currency="USD")  # fails rule 1 of 12
    ctx = build_matching_context()
    decision = evaluate(mandate, intent, ctx)
    assert len(decision.rule_trace) == 12
    assert {entry.rule for entry in decision.rule_trace} == {
        "currency.match",
        "category.allow",
        "merchant.block",
        "temporal.window",
        "cap.unit",
        "cap.total",
        "cap.count",
        "quantity.match",
        "policy.refundable",
        "price.delta",
        "catalog.freshness",
        "integrity.binding",
    }


def test_inputs_digest_is_sha256_prefixed_and_deterministic() -> None:
    mandate = build_locked_mandate()
    intent = build_matching_intent(mandate)
    ctx = build_matching_context()
    d1 = evaluate(mandate, intent, ctx)
    d2 = evaluate(mandate, intent, ctx)
    assert d1.inputs_digest.startswith("sha256:")
    assert d1.inputs_digest == d2.inputs_digest
