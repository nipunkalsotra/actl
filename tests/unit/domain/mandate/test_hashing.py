from actl.domain.mandate.hashing import compute_spec_hash, verify_spec_hash

from .conftest import build_mandate


def test_spec_hash_is_deterministic() -> None:
    a, b = build_mandate(), build_mandate()
    assert compute_spec_hash(a) == compute_spec_hash(b)


def test_spec_hash_is_sha256_prefixed() -> None:
    digest = compute_spec_hash(build_mandate())
    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64


def test_spec_hash_changes_when_content_changes() -> None:
    a = build_mandate()
    b = build_mandate(version=2)
    assert compute_spec_hash(a) != compute_spec_hash(b)


def test_verify_spec_hash_true_when_matching() -> None:
    m = build_mandate()
    locked = m.model_copy(update={"spec_hash": compute_spec_hash(m)})
    assert verify_spec_hash(locked) is True


def test_verify_spec_hash_false_when_tampered() -> None:
    """I-M2: a mandate whose stored hash no longer matches its content is compromised."""
    m = build_mandate()
    stale_hash = compute_spec_hash(m)
    tampered = build_mandate(version=2).model_copy(update={"spec_hash": stale_hash})
    assert verify_spec_hash(tampered) is False


def test_verify_spec_hash_false_when_unset() -> None:
    assert verify_spec_hash(build_mandate()) is False
