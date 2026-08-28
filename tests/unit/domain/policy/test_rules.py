from datetime import UTC, datetime

from actl.domain.policy.reason_codes import ReasonCode
from actl.domain.policy.rules import RULES, compute_intent_hash

from .conftest import build_locked_mandate, build_matching_context, build_matching_intent


def _run(mandate, intent, ctx):
    return {o.rule: o for o in (rule(mandate, intent, ctx) for rule in RULES)}


def test_golden_scenario_passes_all_twelve_rules() -> None:
    mandate = build_locked_mandate()
    intent = build_matching_intent(mandate)
    ctx = build_matching_context()
    outcomes = _run(mandate, intent, ctx)
    assert len(outcomes) == 12
    for name, outcome in outcomes.items():
        assert outcome.result == "pass", f"{name} unexpectedly failed: {outcome.input}"
        assert outcome.reason_code == ReasonCode.OK


def test_currency_mismatch() -> None:
    mandate = build_locked_mandate()
    intent = build_matching_intent(mandate, currency="USD")
    ctx = build_matching_context()
    outcome = _run(mandate, intent, ctx)["currency.match"]
    assert outcome.result == "fail"
    assert outcome.reason_code == ReasonCode.CURRENCY_MISMATCH


def test_category_not_allowed() -> None:
    mandate = build_locked_mandate()
    intent = build_matching_intent(mandate, category="electronics.laptop")
    ctx = build_matching_context()
    outcome = _run(mandate, intent, ctx)["category.allow"]
    assert outcome.result == "fail"
    assert outcome.reason_code == ReasonCode.CATEGORY_NOT_ALLOWED


def test_merchant_blocked() -> None:
    mandate = build_locked_mandate()
    intent = build_matching_intent(mandate, merchant="bad_merchant")
    ctx = build_matching_context()
    outcome = _run(mandate, intent, ctx)["merchant.block"]
    assert outcome.result == "fail"
    assert outcome.reason_code == ReasonCode.MERCHANT_BLOCKED


def test_mandate_not_yet_valid() -> None:
    mandate = build_locked_mandate()
    intent = build_matching_intent(mandate)
    ctx = build_matching_context(now=datetime(2026, 8, 28, 8, 0, 0, tzinfo=UTC))
    outcome = _run(mandate, intent, ctx)["temporal.window"]
    assert outcome.result == "fail"
    assert outcome.reason_code == ReasonCode.MANDATE_NOT_YET_VALID


def test_mandate_expired() -> None:
    mandate = build_locked_mandate()
    intent = build_matching_intent(mandate)
    ctx = build_matching_context(now=datetime(2026, 8, 28, 10, 0, 0, tzinfo=UTC))
    outcome = _run(mandate, intent, ctx)["temporal.window"]
    assert outcome.result == "fail"
    assert outcome.reason_code == ReasonCode.MANDATE_EXPIRED


def test_temporal_window_passes_at_exactly_not_before() -> None:
    mandate = build_locked_mandate()
    intent = build_matching_intent(mandate)
    ctx = build_matching_context(now=datetime(2026, 8, 28, 9, 0, 0, tzinfo=UTC))
    outcome = _run(mandate, intent, ctx)["temporal.window"]
    assert outcome.result == "pass"


def test_unit_cap_exceeded() -> None:
    mandate = build_locked_mandate()
    intent = build_matching_intent(mandate, unit_price_minor=500000)
    ctx = build_matching_context()
    outcome = _run(mandate, intent, ctx)["cap.unit"]
    assert outcome.result == "fail"
    assert outcome.reason_code == ReasonCode.UNIT_CAP_EXCEEDED


def test_budget_exceeded() -> None:
    mandate = build_locked_mandate()
    intent = build_matching_intent(mandate, total_minor=1000000)
    ctx = build_matching_context()
    outcome = _run(mandate, intent, ctx)["cap.total"]
    assert outcome.result == "fail"
    assert outcome.reason_code == ReasonCode.BUDGET_EXCEEDED


def test_budget_exceeded_accounts_for_already_reserved() -> None:
    mandate = build_locked_mandate()
    intent = build_matching_intent(mandate)
    ctx = build_matching_context(reserved_minor=100000)
    outcome = _run(mandate, intent, ctx)["cap.total"]
    assert outcome.result == "fail"
    assert outcome.reason_code == ReasonCode.BUDGET_EXCEEDED


def test_txn_limit_exceeded() -> None:
    mandate = build_locked_mandate()
    intent = build_matching_intent(mandate)
    ctx = build_matching_context(txn_count=1)
    outcome = _run(mandate, intent, ctx)["cap.count"]
    assert outcome.result == "fail"
    assert outcome.reason_code == ReasonCode.TXN_LIMIT_EXCEEDED


def test_quantity_mismatch_nights() -> None:
    mandate = build_locked_mandate()
    intent = build_matching_intent(mandate, nights=4)
    ctx = build_matching_context()
    outcome = _run(mandate, intent, ctx)["quantity.match"]
    assert outcome.result == "fail"
    assert outcome.reason_code == ReasonCode.QUANTITY_MISMATCH


def test_quantity_mismatch_rooms() -> None:
    mandate = build_locked_mandate()
    intent = build_matching_intent(mandate, rooms=2)
    ctx = build_matching_context()
    outcome = _run(mandate, intent, ctx)["quantity.match"]
    assert outcome.result == "fail"
    assert outcome.reason_code == ReasonCode.QUANTITY_MISMATCH


def test_refund_policy_violation() -> None:
    mandate = build_locked_mandate()
    intent = build_matching_intent(mandate, refundable=False)
    ctx = build_matching_context()
    outcome = _run(mandate, intent, ctx)["policy.refundable"]
    assert outcome.result == "fail"
    assert outcome.reason_code == ReasonCode.REFUND_POLICY_VIOLATION


def test_refundable_not_required_when_mandate_allows_it() -> None:
    mandate = build_locked_mandate(require_refundable=False)
    intent = build_matching_intent(mandate, refundable=False)
    ctx = build_matching_context()
    outcome = _run(mandate, intent, ctx)["policy.refundable"]
    assert outcome.result == "pass"


def test_price_drift() -> None:
    mandate = build_locked_mandate()
    intent = build_matching_intent(mandate, current_total_minor=1000000)
    ctx = build_matching_context()
    outcome = _run(mandate, intent, ctx)["price.delta"]
    assert outcome.result == "fail"
    assert outcome.reason_code == ReasonCode.PRICE_DRIFT


def test_price_delta_tolerates_up_to_configured_bps() -> None:
    mandate = build_locked_mandate(max_price_delta_bps=100)  # 1%
    intent = build_matching_intent(mandate, quoted_total_minor=840000, current_total_minor=848000)
    ctx = build_matching_context()
    outcome = _run(mandate, intent, ctx)["price.delta"]
    assert outcome.result == "pass"


def test_price_delta_handles_zero_quoted_and_zero_current() -> None:
    mandate = build_locked_mandate()
    intent = build_matching_intent(
        mandate, quoted_total_minor=0, current_total_minor=0, total_minor=0
    )
    ctx = build_matching_context()
    outcome = _run(mandate, intent, ctx)["price.delta"]
    assert outcome.result == "pass"


def test_price_delta_handles_zero_quoted_nonzero_current() -> None:
    mandate = build_locked_mandate()
    intent = build_matching_intent(mandate, quoted_total_minor=0, current_total_minor=1)
    ctx = build_matching_context()
    outcome = _run(mandate, intent, ctx)["price.delta"]
    assert outcome.result == "fail"
    assert outcome.reason_code == ReasonCode.PRICE_DRIFT


def test_stale_price() -> None:
    mandate = build_locked_mandate()
    intent = build_matching_intent(mandate, catalog_version=117)
    ctx = build_matching_context()
    outcome = _run(mandate, intent, ctx)["catalog.freshness"]
    assert outcome.result == "fail"
    assert outcome.reason_code == ReasonCode.STALE_PRICE


def test_intent_mismatch_on_wrong_mandate_spec_hash() -> None:
    mandate = build_locked_mandate()
    intent = build_matching_intent(mandate, mandate_spec_hash="sha256:wrong")
    ctx = build_matching_context()
    outcome = _run(mandate, intent, ctx)["integrity.binding"]
    assert outcome.result == "fail"
    assert outcome.reason_code == ReasonCode.INTENT_MISMATCH
    assert outcome.input["mandate_spec_hash_match"] is False


def test_intent_mismatch_on_tampered_intent_hash() -> None:
    mandate = build_locked_mandate()
    intent = build_matching_intent(mandate, intent_hash="sha256:tampered")
    ctx = build_matching_context()
    outcome = _run(mandate, intent, ctx)["integrity.binding"]
    assert outcome.result == "fail"
    assert outcome.reason_code == ReasonCode.INTENT_MISMATCH
    assert outcome.input["intent_hash_match"] is False


def test_intent_mismatch_when_mandate_never_locked() -> None:
    """A mandate with spec_hash=None can never satisfy integrity.binding."""
    from actl.domain.mandate.models import (
        Delegate,
        Mandate,
        MandateBounds,
        MandateControls,
        MandateIntent,
        MandateTemporal,
        Principal,
    )

    unlocked = Mandate(
        mandate_id="mdt_unlocked",
        version=1,
        principal=Principal(type="human", id="usr_1"),
        delegate=Delegate(type="agent", id="agt_1", key_id="ed25519:1"),
        intent=MandateIntent(
            category="travel.hotel", location="Goa, IN", check_in="2026-09-12", nights=3, rooms=1
        ),
        bounds=MandateBounds(
            currency="INR",
            max_total_minor=900000,
            max_unit_minor=300000,
            max_transactions=1,
            allowed_categories=["travel.hotel"],
            blocked_merchants=[],
            require_refundable=True,
            max_price_delta_bps=0,
        ),
        temporal=MandateTemporal(
            not_before="2026-08-28T09:00:00.000Z",
            expires_at="2026-08-28T09:30:00.000Z",
            quote_ttl_s=120,
        ),
        controls=MandateControls(human_confirm_required=True, revocable=True),
    )
    intent = build_matching_intent(unlocked, mandate_spec_hash="sha256:claimed-but-never-locked")
    ctx = build_matching_context()
    outcome = _run(unlocked, intent, ctx)["integrity.binding"]
    assert outcome.result == "fail"


def test_compute_intent_hash_changes_when_content_changes() -> None:
    mandate = build_locked_mandate()
    a = build_matching_intent(mandate)
    b = build_matching_intent(mandate, unit_price_minor=1)
    assert compute_intent_hash(a) != compute_intent_hash(b)
