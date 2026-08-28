import pytest

from actl.domain.mandate.models import (
    Delegate,
    Mandate,
    MandateBounds,
    MandateControls,
    MandateIntent,
    MandateSignature,
    MandateTemporal,
    Principal,
)


def build_mandate(**overrides: object) -> Mandate:
    """§8.1's own example mandate, as a Mandate instance."""
    defaults: dict[str, object] = {
        "mandate_id": "mdt_01JX8Z6QK4T2N9V0",
        "version": 1,
        "principal": Principal(type="human", id="usr_7QP2"),
        "delegate": Delegate(type="agent", id="agt_buyer_01", key_id="ed25519:9f31c2"),
        "intent": MandateIntent(
            category="travel.hotel",
            location="Goa, IN",
            check_in="2026-09-12",
            nights=3,
            rooms=1,
        ),
        "bounds": MandateBounds(
            currency="INR",
            max_total_minor=900000,
            max_unit_minor=300000,
            max_transactions=1,
            allowed_categories=["travel.hotel"],
            blocked_merchants=[],
            require_refundable=True,
            max_price_delta_bps=0,
        ),
        "temporal": MandateTemporal(
            not_before="2026-08-28T09:00:00.000Z",
            expires_at="2026-08-28T09:30:00.000Z",
            quote_ttl_s=120,
        ),
        "controls": MandateControls(human_confirm_required=True, revocable=True),
    }
    defaults.update(overrides)
    return Mandate(**defaults)  # type: ignore[arg-type]


def build_signature(
    alg: str = "HMAC-SHA256", key_id: str = "mk_1", value: str = ""
) -> MandateSignature:
    return MandateSignature(alg=alg, key_id=key_id, value=value)


@pytest.fixture
def sample_mandate() -> Mandate:
    return build_mandate()
