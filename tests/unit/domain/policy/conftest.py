from datetime import UTC, datetime

from actl.domain.mandate.hashing import compute_spec_hash
from actl.domain.mandate.models import (
    Delegate,
    Mandate,
    MandateBounds,
    MandateControls,
    MandateIntent,
    MandateTemporal,
    Principal,
)
from actl.domain.policy.rules import PolicyContext, PurchaseIntent, compute_intent_hash

NOW = datetime(2026, 8, 28, 9, 10, 0, tzinfo=UTC)


def build_locked_mandate(**bounds_overrides: object) -> Mandate:
    bounds_defaults: dict[str, object] = {
        "currency": "INR",
        "max_total_minor": 900000,
        "max_unit_minor": 300000,
        "max_transactions": 1,
        "allowed_categories": ["travel.hotel"],
        "blocked_merchants": ["bad_merchant"],
        "require_refundable": True,
        "max_price_delta_bps": 0,
    }
    bounds_defaults.update(bounds_overrides)
    draft = Mandate(
        mandate_id="mdt_01JX8Z6QK4T2N9V0",
        version=1,
        principal=Principal(type="human", id="usr_7QP2"),
        delegate=Delegate(type="agent", id="agt_buyer_01", key_id="ed25519:9f31c2"),
        intent=MandateIntent(
            category="travel.hotel",
            location="Goa, IN",
            check_in="2026-09-12",
            nights=3,
            rooms=1,
        ),
        bounds=MandateBounds(**bounds_defaults),  # type: ignore[arg-type]
        temporal=MandateTemporal(
            not_before="2026-08-28T09:00:00.000Z",
            expires_at="2026-08-28T09:30:00.000Z",
            quote_ttl_s=120,
        ),
        controls=MandateControls(human_confirm_required=True, revocable=True),
    )
    return draft.model_copy(update={"spec_hash": compute_spec_hash(draft)})


def build_matching_intent(mandate: Mandate, **overrides: object) -> PurchaseIntent:
    """An intent that passes all twelve rules against `mandate`, unless
    overridden. `intent_hash` is recomputed automatically after overrides so
    only the rule under test is perturbed — pass intent_hash explicitly to
    test integrity.binding's tamper detection instead."""
    defaults: dict[str, object] = {
        "currency": "INR",
        "category": "travel.hotel",
        "merchant": "good_merchant",
        "unit_price_minor": 280000,
        "total_minor": 840000,
        "nights": 3,
        "rooms": 1,
        "refundable": True,
        "quoted_total_minor": 840000,
        "current_total_minor": 840000,
        "catalog_version": 118,
        "mandate_spec_hash": mandate.spec_hash,
        "intent_hash": "",
    }
    explicit_hash = "intent_hash" in overrides
    defaults.update(overrides)
    intent = PurchaseIntent(**defaults)  # type: ignore[arg-type]
    if not explicit_hash:
        intent = intent.model_copy(update={"intent_hash": compute_intent_hash(intent)})
    return intent


def build_matching_context(**overrides: object) -> PolicyContext:
    defaults: dict[str, object] = {
        "now": NOW,
        "reserved_minor": 0,
        "txn_count": 0,
        "catalog_version": 118,
        "decision_id": "dec_01JX8Z7B3C",
        "decision_ttl_s": 30,
    }
    defaults.update(overrides)
    return PolicyContext(**defaults)  # type: ignore[arg-type]
