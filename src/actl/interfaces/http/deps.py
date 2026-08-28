"""FastAPI dependencies. Nothing here reads os.environ or calls
datetime.now() directly (§26) -- routers get a UnitOfWork and a Clock
through these, never build either themselves.

`get_payment_provider` reads `request.app.state.payment_provider` rather
than constructing an adapter itself: `actl.interfaces` is one of the
import-linter contract's forbidden `source_modules` for reaching
`infrastructure.providers.razorpay` (§28 P5) — the concrete adapter is
built once in `actl.main`'s lifespan (outside that contract's scope) and
handed down, the same pattern `app.state.engine` already uses."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast

from fastapi import Request

from actl.application.ports import PaymentProvider
from actl.infrastructure.db.uow import UnitOfWork
from actl.platform.clock import Clock, SystemClock

_clock: Clock = SystemClock()


async def get_uow() -> AsyncIterator[UnitOfWork]:
    async with UnitOfWork() as uow:
        yield uow


def get_clock() -> Clock:
    return _clock


def get_payment_provider(request: Request) -> PaymentProvider:
    return cast(PaymentProvider, request.app.state.payment_provider)
