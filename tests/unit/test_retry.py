import pytest

from actl.platform.retry import RetryExhausted, retry_with_full_jitter


async def test_retries_until_success() -> None:
    calls = {"n": 0}

    async def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("boom")
        return "ok"

    async def no_sleep(_: float) -> None:
        return None

    result = await retry_with_full_jitter(flaky, max_attempts=5, sleep=no_sleep)
    assert result == "ok"
    assert calls["n"] == 3


async def test_raises_retry_exhausted_after_max_attempts() -> None:
    async def always_fails() -> None:
        raise ValueError("boom")

    async def no_sleep(_: float) -> None:
        return None

    with pytest.raises(RetryExhausted) as exc_info:
        await retry_with_full_jitter(always_fails, max_attempts=3, sleep=no_sleep)
    assert exc_info.value.attempts == 3


async def test_jitter_delay_is_within_full_jitter_bounds() -> None:
    delays: list[float] = []

    async def fails_once_then_ok() -> str:
        if not delays:
            raise ValueError("boom")
        return "ok"

    async def capture_sleep(delay: float) -> None:
        delays.append(delay)

    await retry_with_full_jitter(
        fails_once_then_ok,
        max_attempts=2,
        base_delay_s=1.0,
        max_delay_s=10.0,
        sleep=capture_sleep,
    )
    assert len(delays) == 1
    assert 0.0 <= delays[0] <= 1.0
