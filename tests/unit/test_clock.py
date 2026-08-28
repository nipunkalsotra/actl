from datetime import UTC, datetime, timedelta

import pytest

from actl.platform.clock import FrozenClock, SystemClock


def test_system_clock_is_utc_and_aware() -> None:
    now = SystemClock().now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


def test_frozen_clock_stays_fixed_until_advanced() -> None:
    start = datetime(2026, 8, 28, 9, 0, 0, tzinfo=UTC)
    clock = FrozenClock(start)
    assert clock.now() == start
    clock.advance(timedelta(seconds=30))
    assert clock.now() == start + timedelta(seconds=30)


def test_frozen_clock_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        FrozenClock(datetime(2026, 8, 28))
