"""Background process: webhook processing, the reconciliation poller (§28
P5), and the optional Monad Testnet anchor loop (§28 P11). Outbox relay,
DLQ drainer land with whichever later phase owns them (§5.1) -- every loop
here is independently at-least-once (a crash mid-tick just means the next
tick picks up the same unprocessed rows) and every handler they call is
idempotent by construction (§28 P5 ADR, §28 P11 instruction 4).
"""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application.audit_service import DEFAULT_CHAIN_ID
from actl.application.payment_service import (
    process_unprocessed_webhooks,
    reconcile_non_terminal_orders,
)
from actl.application.ports import PaymentProvider
from actl.config import settings
from actl.infrastructure.anchor.factory import build_anchor_worker
from actl.infrastructure.anchor.monad_testnet import (
    AnchorConflictError,
    AnchorSubmission,
    MonadAnchor,
    TransientAnchorError,
)
from actl.infrastructure.db.repositories.audit_checkpoints import AuditCheckpointRecord
from actl.infrastructure.db.uow import UnitOfWork
from actl.infrastructure.providers.factory import build_payment_provider
from actl.platform import metrics, tracing
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import Clock, SystemClock
from actl.platform.errors import CircuitOpenError
from actl.platform.logging import configure_logging, get_logger
from actl.platform.retry import RetryExhausted, retry_with_full_jitter

configure_logging(level=settings.log_level, json_format=settings.log_format == "json")
logger = get_logger(__name__)

WEBHOOK_POLL_INTERVAL_S = 1.0
RECONCILE_POLL_INTERVAL_S = 10.0
ANCHOR_POLL_INTERVAL_S = 15.0


async def _webhook_loop(clock: Clock) -> None:
    while True:
        try:
            with tracing.span("worker.webhook_tick"):
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
            with tracing.span("worker.reconcile_tick"):
                async with UnitOfWork() as uow:
                    outcomes = await reconcile_non_terminal_orders(uow, provider, clock, breaker)
            if outcomes:
                logger.info("worker.reconciled", count=len(outcomes))
        except Exception:
            logger.exception("worker.reconcile_loop_error")
        await asyncio.sleep(RECONCILE_POLL_INTERVAL_S)


async def _anchor_checkpoint_with_retry(
    client: MonadAnchor, breaker: CircuitBreaker, checkpoint: AuditCheckpointRecord
) -> AnchorSubmission:
    async def _attempt() -> AnchorSubmission:
        return await breaker.call(
            lambda: client.anchor_checkpoint(
                start_seq=checkpoint.from_seq,
                end_seq=checkpoint.to_seq,
                merkle_root_hex=checkpoint.merkle_root,
            )
        )

    return await retry_with_full_jitter(
        _attempt, max_attempts=settings.max_retry_attempts, retry_on=(TransientAnchorError,)
    )


async def _anchor_tick(
    client: MonadAnchor,
    clock: Clock,
    breaker: CircuitBreaker,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    """One poll of the `audit_checkpoints` outbox: every 'unanchored' row is
    a unit of enqueued anchor work (§28 P11 instruction 4 -- "enqueue
    required anchor work through the existing outbox/worker flow" reuses
    `audit_checkpoints` itself as that outbox, rather than a second table).
    Each checkpoint is isolated in its own try/except so one bad checkpoint
    (a permanent conflict, an exhausted retry) never blocks the rest of the
    tick's batch.

    `session_factory` defaults to None (UnitOfWork's own real-DB default,
    matching `_webhook_loop`/`_reconcile_loop`'s existing shape) -- tests
    inject an isolated testcontainers factory instead of touching the real
    configured database (tests/integration/anchor/test_anchor_worker_loop.py)."""
    anchored = 0
    async with UnitOfWork(session_factory) as uow:
        pending = await uow.audit_checkpoints.list_unanchored()
        for checkpoint in pending:
            try:
                submission = await _anchor_checkpoint_with_retry(client, breaker, checkpoint)
                await uow.audit_checkpoints.mark_anchored(
                    checkpoint.to_seq,
                    tx_hash=submission.tx_hash,
                    chain_id=submission.chain_id,
                    contract_address=submission.contract_address,
                    anchored_at=clock.now(),
                )
                outcome = "already_anchored" if submission.already_anchored else "anchored"
                metrics.anchor_submissions_total.labels(outcome=outcome).inc()
                logger.info("worker.anchor_succeeded", to_seq=checkpoint.to_seq, outcome=outcome)
                anchored += 1
            except AnchorConflictError as exc:
                await uow.audit_checkpoints.mark_conflict(checkpoint.to_seq, error=str(exc))
                metrics.anchor_submissions_total.labels(outcome="conflict").inc()
                logger.error("worker.anchor_conflict", to_seq=checkpoint.to_seq, error=str(exc))
            except (RetryExhausted, CircuitOpenError) as exc:
                await uow.audit_checkpoints.record_attempt_failure(
                    checkpoint.to_seq, error=str(exc)
                )
                metrics.anchor_submissions_total.labels(outcome="transient_failure").inc()
                logger.warning(
                    "worker.anchor_retry_exhausted", to_seq=checkpoint.to_seq, error=str(exc)
                )
        await uow.commit()
    return anchored


async def _anchor_loop(client: MonadAnchor, clock: Clock, breaker: CircuitBreaker) -> None:
    while True:
        try:
            with tracing.span("worker.anchor_tick"):
                anchored = await _anchor_tick(client, clock, breaker)
            if anchored:
                logger.info("worker.anchored", count=anchored)
        except Exception:
            logger.exception("worker.anchor_loop_error")
        await asyncio.sleep(ANCHOR_POLL_INTERVAL_S)


async def main() -> None:
    logger.info("worker.startup", app_env=settings.app_env)
    clock = SystemClock()
    provider = build_payment_provider(settings)
    breaker = CircuitBreaker(name="razorpay", clock=clock)
    loops = [_webhook_loop(clock), _reconcile_loop(provider, clock, breaker)]

    # §28 P11: no-op (default) means no anchor loop at all -- zero
    # overhead, zero RPC calls, never affects the two loops above.
    anchor_client = build_anchor_worker(settings, audit_chain_id=DEFAULT_CHAIN_ID)
    if anchor_client is not None:
        anchor_breaker = CircuitBreaker(name="monad-anchor", clock=clock)
        loops.append(_anchor_loop(anchor_client, clock, anchor_breaker))

    try:
        await asyncio.gather(*loops)
    except asyncio.CancelledError:
        pass
    finally:
        aclose = getattr(provider, "aclose", None)
        if aclose is not None:
            await aclose()
        logger.info("worker.shutdown")


if __name__ == "__main__":
    asyncio.run(main())
