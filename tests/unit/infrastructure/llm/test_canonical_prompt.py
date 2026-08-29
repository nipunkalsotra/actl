"""§17.3: "SHA-256 of the normalised prompt" -- determinism and
sensitivity to every input the cache/replay key must actually depend on.
"""

from __future__ import annotations

from actl.infrastructure.llm.canonical_prompt import canonical_prompt_key


def test_same_inputs_produce_the_same_key() -> None:
    a = canonical_prompt_key(mode="json", model="m", system="s", user="u")
    b = canonical_prompt_key(mode="json", model="m", system="s", user="u")
    assert a == b


def test_key_is_a_64_char_hex_sha256_digest() -> None:
    key = canonical_prompt_key(mode="json", model="m", system="s", user="u")
    assert len(key) == 64
    int(key, 16)  # raises if not hex


def test_different_user_text_produces_a_different_key() -> None:
    a = canonical_prompt_key(mode="json", model="m", system="s", user="u1")
    b = canonical_prompt_key(mode="json", model="m", system="s", user="u2")
    assert a != b


def test_different_mode_produces_a_different_key() -> None:
    a = canonical_prompt_key(mode="json", model="m", system="s", user="u")
    b = canonical_prompt_key(mode="text", model="m", system="s", user="u")
    assert a != b


def test_different_model_produces_a_different_key() -> None:
    a = canonical_prompt_key(mode="json", model="m1", system="s", user="u")
    b = canonical_prompt_key(mode="json", model="m2", system="s", user="u")
    assert a != b
