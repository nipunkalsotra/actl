"""jcs() verified against the RFC 8785 reference implementation's own test
vectors (github.com/cyberphone/json-canonicalization/tree/master/testdata),
fetched verbatim into rfc8785_vectors/ — no hand-transcribed unicode."""

import json
from pathlib import Path

import pytest

from actl.domain.audit.canonical import jcs

VECTORS_DIR = Path(__file__).parent / "rfc8785_vectors"
VECTOR_NAMES = ["arrays", "french", "structures", "unicode", "values", "weird"]


@pytest.mark.parametrize("name", VECTOR_NAMES)
def test_matches_official_rfc8785_vector(name: str) -> None:
    input_bytes = (VECTORS_DIR / f"{name}.input.json").read_bytes()
    expected = (VECTORS_DIR / f"{name}.output.json").read_bytes().decode("utf-8")
    value = json.loads(input_bytes)
    assert jcs(value) == expected


def test_matches_architecture_doc_example() -> None:
    """§28 P1 exit criteria: jcs({'b':1,'a':[2,3]}) == '{"a":[2,3],"b":1}'."""
    assert jcs({"b": 1, "a": [2, 3]}) == '{"a":[2,3],"b":1}'


def test_negative_zero_collapses_to_zero() -> None:
    assert jcs(-0.0) == "0"


def test_forward_slash_is_not_escaped() -> None:
    assert jcs("a/b") == '"a/b"'


def test_integers_are_not_routed_through_float_formatting() -> None:
    # int stays exact even far outside float64's safe integer range.
    assert jcs(2**60 + 1) == str(2**60 + 1)


def test_bool_is_not_encoded_as_a_number() -> None:
    assert jcs(True) == "true"
    assert jcs([True, False]) == "[true,false]"


def test_rejects_nan_and_infinity() -> None:
    with pytest.raises(ValueError):
        jcs(float("nan"))
    with pytest.raises(ValueError):
        jcs(float("inf"))


def test_rejects_non_json_type() -> None:
    with pytest.raises(TypeError):
        jcs(object())
