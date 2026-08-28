"""UnitOfWork: the only way application code touches the database (§28 P2
Key decision). One AsyncSession, one transaction, exposing every repository.
Explicit `commit()` is required — exiting the `async with` block without
calling it rolls back, so a caller can never partially commit by forgetting
a step."""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.infrastructure.db.engine import get_session_factory
from actl.infrastructure.db.repositories.audit_checkpoints import AuditCheckpointRepository
from actl.infrastructure.db.repositories.audit_log import AuditLogRepository
from actl.infrastructure.db.repositories.decisions import DecisionRepository
from actl.infrastructure.db.repositories.idempotency_keys import IdempotencyKeyRepository
from actl.infrastructure.db.repositories.ledger_entries import LedgerEntryRepository
from actl.infrastructure.db.repositories.mandates import MandateRepository
from actl.infrastructure.db.repositories.orders import OrderRepository
from actl.infrastructure.db.repositories.outbox import OutboxRepository
from actl.infrastructure.db.repositories.payments import PaymentRepository
from actl.infrastructure.db.repositories.quotes import QuoteRepository
from actl.infrastructure.db.repositories.webhook_events import WebhookEventRepository


class UnitOfWork:
    mandates: MandateRepository
    decisions: DecisionRepository
    quotes: QuoteRepository
    orders: OrderRepository
    payments: PaymentRepository
    ledger_entries: LedgerEntryRepository
    audit_log: AuditLogRepository
    audit_checkpoints: AuditCheckpointRepository
    outbox: OutboxRepository
    webhook_events: WebhookEventRepository
    idempotency_keys: IdempotencyKeyRepository

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        self._session_factory = session_factory or get_session_factory()
        self._session: AsyncSession | None = None
        self._committed = False

    async def __aenter__(self) -> UnitOfWork:
        self._session = self._session_factory()
        self._committed = False
        session = self._session
        self.mandates = MandateRepository(session)
        self.decisions = DecisionRepository(session)
        self.quotes = QuoteRepository(session)
        self.orders = OrderRepository(session)
        self.payments = PaymentRepository(session)
        self.ledger_entries = LedgerEntryRepository(session)
        self.audit_log = AuditLogRepository(session)
        self.audit_checkpoints = AuditCheckpointRepository(session)
        self.outbox = OutboxRepository(session)
        self.webhook_events = WebhookEventRepository(session)
        self.idempotency_keys = IdempotencyKeyRepository(session)
        return self

    async def commit(self) -> None:
        assert self._session is not None, "UnitOfWork used outside 'async with'"
        await self._session.commit()
        self._committed = True

    async def rollback(self) -> None:
        assert self._session is not None, "UnitOfWork used outside 'async with'"
        await self._session.rollback()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self._session is not None
        try:
            if exc_type is not None or not self._committed:
                await self._session.rollback()
        finally:
            await self._session.close()
            self._session = None
