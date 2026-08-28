from datetime import UTC, datetime, timedelta

import pytest

from actl.platform.breaker import BreakerState, CircuitBreaker
from actl.platform.clock import FrozenClock
from actl.platform.errors import CircuitOpenError


async def test_opens_after_consecutive_failures() -> None:
    clock = FrozenClock(datetime(2026, 8, 28, 9, 0, 0, tzinfo=UTC))
    breaker = CircuitBreaker(
        "test", clock=clock, failure_threshold=2, recovery_timeout=timedelta(seconds=10)
    )

    async def fail() -> None:
        raise ValueError("boom")

    for _ in range(2):
        with pytest.raises(ValueError):
            await breaker.call(fail)

    assert breaker.state is BreakerState.OPEN
    with pytest.raises(CircuitOpenError):
        await breaker.call(fail)


async def test_half_opens_after_recovery_timeout_and_closes_on_success() -> None:
    clock = FrozenClock(datetime(2026, 8, 28, 9, 0, 0, tzinfo=UTC))
    breaker = CircuitBreaker(
        "test", clock=clock, failure_threshold=1, recovery_timeout=timedelta(seconds=10)
    )

    async def fail() -> None:
        raise ValueError("boom")

    async def succeed() -> str:
        return "ok"

    with pytest.raises(ValueError):
        await breaker.call(fail)
    assert breaker.state is BreakerState.OPEN

    clock.advance(timedelta(seconds=11))
    assert breaker.state is BreakerState.HALF_OPEN

    result = await breaker.call(succeed)
    assert result == "ok"
    assert breaker.state is BreakerState.CLOSED
