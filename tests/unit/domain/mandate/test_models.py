import pytest
from pydantic import ValidationError

from .conftest import build_mandate


def test_builds_from_the_architecture_doc_example(sample_mandate) -> None:
    assert sample_mandate.mandate_id == "mdt_01JX8Z6QK4T2N9V0"
    assert sample_mandate.bounds.max_total_minor == 900000
    assert sample_mandate.spec_hash is None
    assert sample_mandate.model_dump(by_alias=True)["schema"] == "actl.mandate/v1"


def test_money_fields_reject_float() -> None:
    with pytest.raises(ValidationError):
        build_mandate(
            bounds={
                "currency": "INR",
                "max_total_minor": 900000.0,
                "max_unit_minor": 300000,
                "max_transactions": 1,
                "allowed_categories": ["travel.hotel"],
                "blocked_merchants": [],
                "require_refundable": True,
                "max_price_delta_bps": 0,
            }
        )


def test_mandate_is_immutable(sample_mandate) -> None:
    with pytest.raises(ValidationError):
        sample_mandate.version = 2  # type: ignore[misc]
