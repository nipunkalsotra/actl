"""Injected time. Nothing in this codebase is allowed to call datetime.now() directly."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Return the current instant, timezone-aware, UTC."""
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FrozenClock:
    """Deterministic clock for tests. Time only moves when told to."""

    def __init__(self, at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("FrozenClock requires a timezone-aware datetime")
        self._at = at

    def now(self) -> datetime:
        return self._at

    def advance(self, by: timedelta) -> None:
        self._at = self._at + by
