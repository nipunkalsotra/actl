from actl.platform.redaction import redact, redaction_processor


def test_redacts_sensitive_top_level_key() -> None:
    out = redaction_processor(
        None, "info", {"razorpay_key_secret": "sk_live_abc", "order_id": "ord_1"}
    )
    assert out["razorpay_key_secret"] == "***REDACTED***"
    assert out["order_id"] == "ord_1"


def test_redacts_nested_mapping() -> None:
    out = redact({"payload": {"authorization": "Bearer xyz", "amount_minor": 100}})
    assert out["payload"]["authorization"] == "***REDACTED***"
    assert out["payload"]["amount_minor"] == 100
