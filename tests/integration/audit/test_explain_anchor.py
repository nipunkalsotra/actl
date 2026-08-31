"""§28 P11 instruction 5: explain_service's anchor field, at every state a
checkpoint can be in. Reuses test_explain_endpoint.py's own full-
transaction helper (real gate/ledger/saga/payment path) for a realistic
order rather than duplicating that setup.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl import config
from actl.application.audit_service import append_entry
from actl.application.explain_service import explain_order
from actl.domain.audit.events import AuditAction
from actl.infrastructure.db.uow import UnitOfWork
from actl.platform.ids import new_id
from tests.integration.observability.test_explain_endpoint import _run_full_transaction

pytestmark = pytest.mark.asyncio(loop_scope="session")

CHECKPOINT_EVERY = 4


async def _pad_to_next_boundary(
    session_factory: async_sessionmaker[AsyncSession], checkpoint_every: int
) -> None:
    async with UnitOfWork(session_factory) as uow:
        tail = await uow.audit_log.get_tail()
    seq = tail[0] if tail is not None else 0
    while seq % checkpoint_every != 0:
        async with UnitOfWork(session_factory) as uow:
            await append_entry(
                uow,
                trace_id=new_id("trc"),
                actor_type="system",
                actor_id="explain_anchor_test",
                action=AuditAction.MANDATE_LOCKED,
                subject={},
                payload={"nonce": new_id("nonce")},
            )
            await uow.commit()
        seq += 1


async def test_explain_reports_no_anchor_when_no_checkpoint_covers_the_order_yet(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Default real checkpoint interval (64): a single order's ~9 entries
    never cross a boundary on their own -- the common case, and exactly
    what ANCHOR_PROVIDER=noop looks like forever."""
    order_id = await _run_full_transaction(session_factory)

    async with UnitOfWork(session_factory) as uow:
        result = await explain_order(uow, order_id)

    assert result.anchor is None


async def test_explain_reports_unanchored_status_once_a_checkpoint_covers_the_order(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config.settings, "audit_checkpoint_every", CHECKPOINT_EVERY)
    order_id = await _run_full_transaction(session_factory)
    await _pad_to_next_boundary(session_factory, CHECKPOINT_EVERY)

    async with UnitOfWork(session_factory) as uow:
        result = await explain_order(uow, order_id)

    assert result.anchor is not None
    assert result.anchor.status == "unanchored"
    assert result.anchor.tx_hash is None
    assert result.anchor.explorer_url is None


async def test_explain_reports_anchored_status_with_explorer_url(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config.settings, "audit_checkpoint_every", CHECKPOINT_EVERY)
    order_id = await _run_full_transaction(session_factory)
    await _pad_to_next_boundary(session_factory, CHECKPOINT_EVERY)

    async with UnitOfWork(session_factory) as uow:
        pre = await explain_order(uow, order_id)
    assert pre.anchor is not None
    to_seq = pre.anchor.checkpoint_to_seq

    from datetime import UTC, datetime

    async with UnitOfWork(session_factory) as uow:
        await uow.audit_checkpoints.mark_anchored(
            to_seq,
            tx_hash="0x" + "ab" * 32,
            chain_id=10143,
            contract_address="0x5FbDB2315678afecb367f032d93F642f64180aa3",
            anchored_at=datetime.now(UTC),
        )
        await uow.commit()

    async with UnitOfWork(session_factory) as uow:
        result = await explain_order(uow, order_id)

    assert result.anchor is not None
    assert result.anchor.status == "anchored"
    assert result.anchor.chain_id == 10143
    assert result.anchor.contract_address == "0x5FbDB2315678afecb367f032d93F642f64180aa3"
    assert result.anchor.tx_hash == "0x" + "ab" * 32
    assert result.anchor.explorer_url == f"https://testnet.monadscan.com/tx/0x{'ab' * 32}"


async def test_explain_reports_conflict_status(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config.settings, "audit_checkpoint_every", CHECKPOINT_EVERY)
    order_id = await _run_full_transaction(session_factory)
    await _pad_to_next_boundary(session_factory, CHECKPOINT_EVERY)

    async with UnitOfWork(session_factory) as uow:
        pre = await explain_order(uow, order_id)
    assert pre.anchor is not None
    to_seq = pre.anchor.checkpoint_to_seq

    async with UnitOfWork(session_factory) as uow:
        await uow.audit_checkpoints.mark_conflict(to_seq, error="on-chain root disagrees")
        await uow.commit()

    async with UnitOfWork(session_factory) as uow:
        result = await explain_order(uow, order_id)

    assert result.anchor is not None
    assert result.anchor.status == "conflict"
    assert result.anchor.explorer_url is None
