"""§28 P9: deterministic id seeding for golden traces -- off by default,
opt-in only, never leaking between tests.
"""

from __future__ import annotations

import pytest

from actl.platform.ids import new_id, reset_ids, seed_deterministic_ids, ulid


@pytest.fixture(autouse=True)
def _reset_ids_after_each_test() -> None:
    yield
    reset_ids()


def test_default_mode_produces_distinct_real_ulids() -> None:
    a, b = new_id("mdt"), new_id("mdt")
    assert a != b
    assert a.startswith("mdt_")
    assert len(a) == len("mdt_") + 26


def test_same_seed_produces_the_same_sequence() -> None:
    seed_deterministic_ids("golden-demo")
    first_run = [ulid() for _ in range(5)]

    seed_deterministic_ids("golden-demo")
    second_run = [ulid() for _ in range(5)]

    assert first_run == second_run


def test_deterministic_mode_still_produces_distinct_ids_within_one_run() -> None:
    seed_deterministic_ids("golden-demo")
    ids = [ulid() for _ in range(20)]
    assert len(set(ids)) == 20


def test_different_seeds_produce_different_sequences() -> None:
    seed_deterministic_ids("seed-a")
    a = [ulid() for _ in range(3)]
    seed_deterministic_ids("seed-b")
    b = [ulid() for _ in range(3)]
    assert a != b


def test_reset_returns_to_real_random_ids() -> None:
    seed_deterministic_ids("golden-demo")
    deterministic = new_id("mdt")
    reset_ids()
    real_a, real_b = new_id("mdt"), new_id("mdt")
    assert real_a != deterministic
    assert real_a != real_b
