"""Per-dependency circuit breaker (§5, platform layer).

Time comes from an injected Clock, never the wall clock, so trip/recovery
behaviour is deterministically testable with FrozenClock.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import TypeVar

from actl.platform.clock import Clock
from actl.platform.errors import CircuitOpenError

T = TypeVar("T")


class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """Trips OPEN after `failure_threshold` consecutive failures. Stays OPEN
    for `recovery_timeout`, then goes HALF_OPEN and allows exactly one trial
    call to decide whether to close again or re-open."""

    name: str
    clock: Clock
    failure_threshold: int = 5
    recovery_timeout: timedelta = field(default_factory=lambda: timedelta(seconds=30))

    _state: BreakerState = field(default=BreakerState.CLOSED, init=False)
    _consecutive_failures: int = field(default=0, init=False)
    _opened_at: datetime | None = field(default=None, init=False)
    _half_open_trial_in_flight: bool = field(default=False, init=False)

    @property
    def state(self) -> BreakerState:
        if self._state is BreakerState.OPEN and self._opened_at is not None:
            elapsed = self.clock.now() - self._opened_at
            if elapsed >= self.recovery_timeout:
                return BreakerState.HALF_OPEN
        return self._state

    async def call(self, fn: Callable[[], Awaitable[T]]) -> T:
        current = self.state
        if current is BreakerState.OPEN:
            raise CircuitOpenError(f"circuit '{self.name}' is open")
        if current is BreakerState.HALF_OPEN:
            if self._half_open_trial_in_flight:
                raise CircuitOpenError(f"circuit '{self.name}' is half-open (trial in flight)")
            self._half_open_trial_in_flight = True
        try:
            result = await fn()
        except Exception:
            self._on_failure()
            raise
        else:
            self._on_success()
            return result
        finally:
            self._half_open_trial_in_flight = False

    def _on_success(self) -> None:
        self._consecutive_failures = 0
        self._state = BreakerState.CLOSED
        self._opened_at = None

    def _on_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.failure_threshold or self._state is BreakerState.OPEN:
            self._state = BreakerState.OPEN
            self._opened_at = self.clock.now()
