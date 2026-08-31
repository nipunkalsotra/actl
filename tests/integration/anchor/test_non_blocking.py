"""§28 P11 non-negotiable rule, made an explicit, executable proof: "No
payment, ledger, gate, saga, checkout, or audit append action may wait on
or fail because Monad is unavailable." Four angles on the same guarantee:

1. A full demo scenario (real gate/ledger/saga/payment path) completes
   normally with ANCHOR_PROVIDER=monad configured against an unreachable
   RPC -- proof by direct execution, not just by code inspection.
2. append_entry across a real checkpoint boundary stays fast regardless
   of ANCHOR_PROVIDER, because it never reads that setting at all.
3. MonadAnchor itself, pointed at an unreachable host, fails FAST (a
   bounded timeout), not by hanging forever.
4. A worker anchor-tick against an unreachable client swallows the
   failure internally and never blocks or crashes the tick.
"""

from __future__ import annotations

import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl import config, worker
from actl.application.audit_service import append_entry
from actl.domain.audit.events import AuditAction
from actl.infrastructure.anchor.monad_testnet import MonadAnchor, TransientAnchorError
from actl.infrastructure.db.uow import UnitOfWork
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import SystemClock
from actl.platform.ids import new_id
from tests.integration.observability.test_explain_endpoint import _run_full_transaction
from tests.support.scratch_keystore import write_scratch_keystore

pytestmark = pytest.mark.asyncio(loop_scope="session")

# 10.255.255.1 is a reserved, non-routable TEST-NET address (RFC 5737
# family) -- connection attempts to it hang until TCP's own SYN retry
# gives up, which is exactly the "genuinely broken RPC" case a bounded
# client timeout must not be able to hang on. 127.0.0.1 unused ports
# refuse immediately instead, which doesn't exercise the timeout path.
_UNREACHABLE_RPC = "http://10.255.255.1:8545"


@contextmanager
def _unreachable_client(timeout_s: float = 2.0) -> Iterator[MonadAnchor]:
    with tempfile.TemporaryDirectory() as tmp:
        keystore_path = Path(tmp) / "keystore.json"
        write_scratch_keystore(keystore_path, "pw")
        yield MonadAnchor(
            rpc_url=_UNREACHABLE_RPC,
            chain_id=31337,
            contract_address="0x5FbDB2315678afecb367f032d93F642f64180aa3",
            keystore_path=str(keystore_path),
            keystore_password="pw",
            audit_chain_id="actl.audit_log",
            timeout_s=timeout_s,
        )


async def test_demo_scenario_completes_normally_with_broken_monad_config(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """§28 P11 instruction: "demo scenario" -- the real gate/ledger/saga/
    payment path, run for real, with ANCHOR_PROVIDER=monad and a broken
    RPC configured. Passes only because nothing on this path ever reads
    those settings -- proven by execution, not assumption.

    Uses _run_full_transaction (quote -> propose -> capture -> settle ->
    webhook, the same real application code real traffic uses -- see
    tests/integration/observability/test_explain_endpoint.py) rather than
    application.demo.run_scenario: the latter reseeds a *global*
    deterministic-id counter keyed only by scenario name
    (platform/ids.py::seed_deterministic_ids), so a second call with the
    same scenario name against this same shared testcontainers Postgres
    -- which tests/integration/observability/test_secret_redaction.py
    also does -- replays identical primary keys and collides. Real,
    non-seeded ULIDs avoid that entirely, and this is still the real
    money-authorization path, not a bypass of it."""
    monkeypatch.setattr(config.settings, "anchor_provider", "monad")
    monkeypatch.setattr(config.settings, "monad_rpc_url", _UNREACHABLE_RPC)
    monkeypatch.setattr(config.settings, "monad_contract_address", "0x" + "1" * 40)

    order_id = await _run_full_transaction(session_factory)

    async with UnitOfWork(session_factory) as uow:
        order = await uow.orders.get(order_id)
    assert order is not None
    assert order.status == "CAPTURED"


async def _append_one(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with UnitOfWork(session_factory) as uow:
        await append_entry(
            uow,
            trace_id=new_id("trc"),
            actor_type="system",
            actor_id="non_blocking_test",
            action=AuditAction.MANDATE_LOCKED,
            subject={},
            payload={"nonce": new_id("nonce")},
        )
        await uow.commit()


async def test_checkpoint_creation_stays_fast_regardless_of_anchor_provider(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config.settings, "audit_checkpoint_every", 8)
    monkeypatch.setattr(config.settings, "anchor_provider", "monad")
    monkeypatch.setattr(config.settings, "monad_rpc_url", _UNREACHABLE_RPC)

    started = time.monotonic()
    for _ in range(8):
        await _append_one(session_factory)
    elapsed = time.monotonic() - started

    # Generous bound for a real Postgres round-trip x8 -- what matters is
    # that this is nowhere near a network timeout (seconds), proving
    # zero RPC attempts happened on this path.
    assert elapsed < 5.0, f"checkpoint creation took {elapsed:.2f}s -- looks blocked on Monad"


async def test_monad_anchor_fails_fast_on_unreachable_rpc_not_hang_forever() -> None:
    with _unreachable_client() as client:
        started = time.monotonic()
        with pytest.raises(TransientAnchorError):
            await client.anchor_checkpoint(
                start_seq=1, end_seq=64, merkle_root_hex="sha256:" + "ab" * 32
            )
        elapsed = time.monotonic() - started

    # web3.py's HTTPProvider retries connection failures internally (5x
    # with backoff) on top of timeout_s -- discovered empirically, see
    # infrastructure/anchor/monad_testnet.py's constructor comment. The
    # bound here is deliberately generous: what matters is "bounded, never
    # hangs forever," not sub-second latency.
    assert elapsed < 30.0, f"MonadAnchor took {elapsed:.2f}s to fail -- not bounded"


async def test_anchor_tick_with_unreachable_client_never_raises_and_stays_bounded(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worker tick that hits a real, unreachable RPC must not crash the
    tick or block other work -- the failure is caught, recorded, and the
    loop moves on (§28 P11 instruction 4's RetryExhausted/CircuitOpenError
    handling in worker._anchor_tick)."""
    monkeypatch.setattr(config.settings, "audit_checkpoint_every", 4)
    monkeypatch.setattr(config.settings, "max_retry_attempts", 1)

    async with UnitOfWork(session_factory) as uow:
        tail = await uow.audit_log.get_tail()
    seq = tail[0] if tail is not None else 0
    while seq % 4 != 0:
        await _append_one(session_factory)
        seq += 1
    for _ in range(4):
        await _append_one(session_factory)

    with _unreachable_client() as client:
        breaker = CircuitBreaker(name="test-unreachable", clock=SystemClock())

        started = time.monotonic()
        # Must not raise -- errors are caught and recorded per-checkpoint.
        await worker._anchor_tick(client, SystemClock(), breaker, session_factory)
        elapsed = time.monotonic() - started

    # Generous: this shared-session Postgres may hold other tests' own
    # ambient unanchored checkpoints too, each independently taking up to
    # ~15-20s to fail against the unreachable RPC before the breaker's
    # default failure_threshold=5 starts short-circuiting later ones in
    # the same tick. What's under test is "bounded, never hangs forever,"
    # not a specific latency number.
    assert elapsed < 120.0, f"anchor tick took {elapsed:.2f}s against an unreachable RPC"

    # And unrelated audit work immediately after is completely unaffected.
    started = time.monotonic()
    await _append_one(session_factory)
    assert time.monotonic() - started < 2.0
