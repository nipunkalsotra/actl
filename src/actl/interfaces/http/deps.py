"""FastAPI dependencies. Nothing here reads os.environ or calls
datetime.now() directly (§26) -- routers get a UnitOfWork and a Clock
through these, never build either themselves."""

from __future__ import annotations

from collections.abc import AsyncIterator

from actl.infrastructure.db.uow import UnitOfWork
from actl.platform.clock import Clock, SystemClock

_clock: Clock = SystemClock()


async def get_uow() -> AsyncIterator[UnitOfWork]:
    async with UnitOfWork() as uow:
        yield uow


def get_clock() -> Clock:
    return _clock
