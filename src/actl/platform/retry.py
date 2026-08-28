"""Retry with full jitter (AWS "Exponential Backoff And Jitter"):
sleep = uniform(0, min(cap, base * 2**attempt)).

Full jitter, not equal/decorrelated jitter: it is the simplest scheme that
still avoids the thundering-herd retry storms of naive exponential backoff,
and this system has no need for the extra tuning knobs the other variants add.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable


class RetryExhausted(Exception):
    def __init__(self, attempts: int, last_error: BaseException) -> None:
        super().__init__(f"retry exhausted after {attempts} attempt(s): {last_error!r}")
        self.attempts = attempts
        self.last_error = last_error


async def retry_with_full_jitter[T](
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int,
    base_delay_s: float = 0.1,
    max_delay_s: float = 10.0,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    last_error: BaseException | None = None
    for attempt in range(max_attempts):
        try:
            return await fn()
        except retry_on as exc:
            last_error = exc
            if attempt == max_attempts - 1:
                break
            cap = min(max_delay_s, base_delay_s * (2**attempt))
            await sleep(random.uniform(0, cap))
    assert last_error is not None
    raise RetryExhausted(max_attempts, last_error)
