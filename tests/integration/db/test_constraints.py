"""§18.2 constraints and indexes, verified against a real Postgres — "every
money column is BIGINT with a positive-value check constraint" (§28 P2
blocker) plus the indexes and FK/unique constraints §18.2 specifies.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.infrastructure.db.repositories.ledger_entries import LedgerEntryRecord
from actl.infrastructure.db.uow import UnitOfWork
from actl.platform.ids import new_id

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_ledger_entries_rejects_non_positive_amount(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with UnitOfWork(session_factory) as uow:
        await uow.ledger_entries.add(
            LedgerEntryRecord(
                account=new_id("acct"),
                direction="debit",
                amount_minor=0,
                ref_type="order",
                ref_id=new_id("ord"),
            )
        )
        with pytest.raises(IntegrityError):
            await uow.commit()


async def test_ledger_entries_rejects_invalid_direction(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with UnitOfWork(session_factory) as uow:
        await uow.ledger_entries.add(
            LedgerEntryRecord(
                account=new_id("acct"),
                direction="sideways",
                amount_minor=100,
                ref_type="order",
                ref_id=new_id("ord"),
            )
        )
        with pytest.raises(IntegrityError):
            await uow.commit()


async def _constraint_names(session: AsyncSession, table: str) -> set[str]:
    result = await session.execute(
        text(
            "SELECT constraint_name FROM information_schema.table_constraints "
            "WHERE table_name = :table"
        ),
        {"table": table},
    )
    return {row[0] for row in result}


async def _index_names(session: AsyncSession, table: str) -> set[str]:
    result = await session.execute(
        text("SELECT indexname FROM pg_indexes WHERE tablename = :table"), {"table": table}
    )
    return {row[0] for row in result}


async def test_mandates_constraints_and_index_exist(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        constraints = await _constraint_names(session, "mandates")
        assert "locked_has_hash" in constraints
        assert "mandates_max_total_minor_positive" in constraints
        assert "mandates_max_unit_minor_positive" in constraints
        assert "ix_mandates_status_expires_at" in await _index_names(session, "mandates")


async def test_policy_decisions_constraint_and_indexes_exist(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        constraints = await _constraint_names(session, "policy_decisions")
        assert "policy_decisions_verdict_check" in constraints
        indexes = await _index_names(session, "policy_decisions")
        assert "ix_policy_decisions_mandate_id_evaluated_at" in indexes
        assert "ix_policy_decisions_intent_hash" in indexes


async def test_orders_unique_idempotency_key_and_attempt_no_index_exist(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        constraints = await _constraint_names(session, "orders")
        assert "orders_amount_minor_positive" in constraints
        assert any("idempotency_key" in c for c in constraints)
        assert "ix_orders_mandate_id_attempt_no" in await _index_names(session, "orders")


async def test_ledger_entries_constraints_and_indexes_exist(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        constraints = await _constraint_names(session, "ledger_entries")
        assert "ledger_entries_amount_minor_positive" in constraints
        assert "ledger_entries_direction_check" in constraints
        indexes = await _index_names(session, "ledger_entries")
        assert "ix_ledger_entries_account_created_at" in indexes
        assert "ix_ledger_entries_ref_type_ref_id" in indexes


async def test_audit_log_unique_entry_hash_and_indexes_exist(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        constraints = await _constraint_names(session, "audit_log")
        assert any("entry_hash" in c for c in constraints)
        indexes = await _index_names(session, "audit_log")
        assert "ix_audit_log_trace_id" in indexes
        assert "ix_audit_log_action_ts" in indexes
        assert "ix_audit_log_subject_order_id" in indexes


async def test_outbox_and_webhook_events_and_idempotency_keys_exist(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        assert "ix_outbox_published_at_id" in await _index_names(session, "outbox")

        webhook_constraints = await _constraint_names(session, "webhook_events")
        assert any("provider_event_id" in c for c in webhook_constraints)

        idem_constraints = await _constraint_names(session, "idempotency_keys")
        assert "idempotency_keys_state_check" in idem_constraints


async def test_orders_foreign_keys_are_enforced(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """orders.mandate_id / decision_id / quote_id all reference other
    tables (§18.2) — inserting against a nonexistent mandate must fail."""
    async with session_factory() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    "INSERT INTO orders "
                    "(id, mandate_id, decision_id, quote_id, status, amount_minor, "
                    " currency, idempotency_key) "
                    "VALUES (:id, :mandate_id, :decision_id, :quote_id, 'CREATED', 100, "
                    "        'INR', :idem)"
                ),
                {
                    "id": new_id("ord"),
                    "mandate_id": "mdt_does_not_exist",
                    "decision_id": "dec_does_not_exist",
                    "quote_id": "qte_does_not_exist",
                    "idem": new_id("ik"),
                },
            )
        await session.rollback()
