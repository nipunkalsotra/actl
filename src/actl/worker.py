"""Background process: webhook processing and the reconciliation poller
(§28 P5). Outbox relay, Merkle checkpointer, anchor writer, DLQ drainer
land with whichever later phase owns them (§5.1) -- both loops here are
independently at-least-once (a crash mid-tick just means the next tick
picks up the same unprocessed rows) and every handler they call is
idempotent by construction (§28 P5 ADR).
"""

from __future__ import annotations

import asyncio

from actl.application.payment_service import (
    process_unprocessed_webhooks,
    reconcile_non_terminal_orders,
)
from actl.application.ports import PaymentProvider
from actl.config import settings
from actl.infrastructure.db.uow import UnitOfWork
from actl.infrastructure.providers.factory import build_payment_provider
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import Clock, SystemClock
from actl.platform.logging import configure_logging, get_logger

configure_logging(level=settings.log_level, json_format=settings.log_format == "json")
logger = get_logger(__name__)

WEBHOOK_POLL_INTERVAL_S = 1.0
RECONCILE_POLL_INTERVAL_S = 10.0


async def _webhook_loop(clock: Clock) -> None:
    while True:
        try:
            async with UnitOfWork() as uow:
                processed = await process_unprocessed_webhooks(uow, clock)
            if processed:
                logger.info("worker.webhooks_processed", count=len(processed))
        except Exception:
            logger.exception("worker.webhook_loop_error")
        await asyncio.sleep(WEBHOOK_POLL_INTERVAL_S)


async def _reconcile_loop(provider: PaymentProvider, clock: Clock, breaker: CircuitBreaker) -> None:
    while True:
        try:
            async with UnitOfWork() as uow:
                outcomes = await reconcile_non_terminal_orders(uow, provider, clock, breaker)
            if outcomes:
                logger.info("worker.reconciled", count=len(outcomes))
        except Exception:
            logger.exception("worker.reconcile_loop_error")
        await asyncio.sleep(RECONCILE_POLL_INTERVAL_S)


async def main() -> None:
    logger.info("worker.startup", app_env=settings.app_env)
    clock = SystemClock()
    provider = build_payment_provider(settings)
    breaker = CircuitBreaker(name="razorpay", clock=clock)
    try:
        await asyncio.gather(_webhook_loop(clock), _reconcile_loop(provider, clock, breaker))
    except asyncio.CancelledError:
        pass
    finally:
        aclose = getattr(provider, "aclose", None)
        if aclose is not None:
            await aclose()
        logger.info("worker.shutdown")


if __name__ == "__main__":
    asyncio.run(main())
