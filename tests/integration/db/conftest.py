"""Domain-object test helpers for tests/integration/db. Container/engine/
session fixtures (postgres_url, engine, session_factory) live in the parent
tests/integration/conftest.py, shared with tests/integration/audit."""

from __future__ import annotations

from actl.config import settings
from actl.domain.mandate.hashing import compute_spec_hash
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
from actl.domain.mandate.signing import sign_spec_hash
from actl.platform.ids import new_id


def make_locked_mandate() -> Mandate:
    """A fresh, uniquely-ided, locked Mandate for one test — fresh ids keep
    tests repeatable without needing per-test database isolation."""
    draft = Mandate(
        mandate_id=new_id("mdt"),
        version=1,
        principal=Principal(type="human", id="usr_test"),
        delegate=Delegate(type="agent", id="agt_test", key_id="ed25519:test"),
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
            not_before="2026-01-01T00:00:00.000Z",
            expires_at="2027-01-01T00:00:00.000Z",
            quote_ttl_s=120,
        ),
        controls=MandateControls(human_confirm_required=True, revocable=True),
    )
    spec_hash = compute_spec_hash(draft)
    signature = MandateSignature(
        alg="HMAC-SHA256",
        key_id="mk_1",
        value=sign_spec_hash(spec_hash, settings.mandate_signing_key.encode("utf-8")),
    )
    return draft.model_copy(update={"spec_hash": spec_hash, "signature": signature})
