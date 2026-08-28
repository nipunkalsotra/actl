from actl.domain.mandate.signing import sign_spec_hash, verify_signature

KEY = b"test-signing-key"
OTHER_KEY = b"a-different-key"
SPEC_HASH = "sha256:6f1b1234"


def test_sign_then_verify_round_trips() -> None:
    sig = sign_spec_hash(SPEC_HASH, KEY)
    assert verify_signature(SPEC_HASH, KEY, sig) is True


def test_verify_fails_with_wrong_key() -> None:
    sig = sign_spec_hash(SPEC_HASH, KEY)
    assert verify_signature(SPEC_HASH, OTHER_KEY, sig) is False


def test_verify_fails_when_spec_hash_tampered() -> None:
    sig = sign_spec_hash(SPEC_HASH, KEY)
    assert verify_signature("sha256:deadbeef", KEY, sig) is False


def test_signature_is_deterministic_for_same_key_and_hash() -> None:
    assert sign_spec_hash(SPEC_HASH, KEY) == sign_spec_hash(SPEC_HASH, KEY)
