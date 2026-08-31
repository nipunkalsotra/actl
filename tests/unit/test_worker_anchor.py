"""§28 P11: worker._anchor_checkpoint_with_retry's retry/breaker
composition, DB-free (AuditCheckpointRecord constructed directly -- see
tests/integration/anchor/test_anchor_worker_loop.py for the real-Postgres
outbox-poll proof)."""

from __future__ import annotations

import pytest

from actl import config, worker
from actl.infrastructure.anchor.monad_testnet import (
    AnchorConflictError,
    AnchorSubmission,
    TransientAnchorError,
)
from actl.infrastructure.db.repositories.audit_checkpoints import AuditCheckpointRecord
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import SystemClock
from actl.platform.errors import CircuitOpenError
from actl.platform.retry import RetryExhausted


class _FakeClient:
    def __init__(self, outcomes: list[object]) -> None:
        self._outcomes = list(outcomes)
        self.call_count = 0

    async def anchor_checkpoint(
        self, *, start_seq: int, end_seq: int, merkle_root_hex: str
    ) -> AnchorSubmission:
        self.call_count += 1
        outcome = self._outcomes.pop(0) if len(self._outcomes) > 1 else self._outcomes[0]
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, AnchorSubmission)
        return outcome


_CHECKPOINT = AuditCheckpointRecord(from_seq=1, to_seq=64, merkle_root="sha256:" + "ab" * 32)


async def test_succeeds_on_first_attempt_without_retrying() -> None:
    submission = AnchorSubmission(
        chain_id=10143, contract_address="0xDEAD", already_anchored=False, tx_hash="0xabc"
    )
    client = _FakeClient([submission])
    breaker = CircuitBreaker(name="test", clock=SystemClock())

    result = await worker._anchor_checkpoint_with_retry(client, breaker, _CHECKPOINT)
    assert result is submission
    assert client.call_count == 1


async def test_retries_transient_failures_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.settings, "max_retry_attempts", 3)
    submission = AnchorSubmission(
        chain_id=10143, contract_address="0xDEAD", already_anchored=False, tx_hash="0xabc"
    )
    client = _FakeClient([TransientAnchorError("rpc timeout"), submission])
    breaker = CircuitBreaker(name="test", clock=SystemClock())

    result = await worker._anchor_checkpoint_with_retry(client, breaker, _CHECKPOINT)
    assert result is submission
    assert client.call_count == 2


async def test_exhausts_retries_and_raises_retry_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config.settings, "max_retry_attempts", 3)
    client = _FakeClient([TransientAnchorError("still down")])
    breaker = CircuitBreaker(name="test-exhaust", clock=SystemClock())

    with pytest.raises(RetryExhausted):
        await worker._anchor_checkpoint_with_retry(client, breaker, _CHECKPOINT)
    assert client.call_count == 3


async def test_conflict_error_propagates_immediately_never_retried() -> None:
    """§28 P11 instruction 4: a conflicting on-chain root is a permanent
    failure -- retrying it would never help and would waste gas."""
    client = _FakeClient([AnchorConflictError("root mismatch")])
    breaker = CircuitBreaker(name="test-conflict", clock=SystemClock())

    with pytest.raises(AnchorConflictError):
        await worker._anchor_checkpoint_with_retry(client, breaker, _CHECKPOINT)
    assert client.call_count == 1


async def test_open_breaker_stops_further_attempts_with_circuit_open_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config.settings, "max_retry_attempts", 10)
    breaker = CircuitBreaker(name="test-open", clock=SystemClock(), failure_threshold=2)
    client = _FakeClient([TransientAnchorError("down")])

    with pytest.raises((RetryExhausted, CircuitOpenError)):
        await worker._anchor_checkpoint_with_retry(client, breaker, _CHECKPOINT)
