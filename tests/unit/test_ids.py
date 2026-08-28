import re

from actl.platform.ids import new_id, ulid

_CROCKFORD = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")


def test_ulid_is_26_crockford_chars() -> None:
    value = ulid()
    assert len(value) == 26
    assert set(value) <= _CROCKFORD


def test_ulid_is_unique_across_calls() -> None:
    assert len({ulid() for _ in range(200)}) == 200


def test_new_id_applies_prefix() -> None:
    value = new_id("mdt")
    assert re.fullmatch(r"mdt_[0-9A-HJKMNP-TV-Z]{26}", value)
