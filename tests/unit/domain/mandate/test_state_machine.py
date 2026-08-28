from datetime import UTC, datetime

import pytest

from actl.domain.mandate.state_machine import (
    GuardRejected,
    InvalidTransition,
    MandateStatus,
    TransitionGuardContext,
    transition,
)

NOW = datetime(2026, 8, 28, 9, 0, 0, tzinfo=UTC)
LATER = datetime(2026, 8, 28, 10, 0, 0, tzinfo=UTC)


def test_draft_to_pending_confirm() -> None:
    assert transition(MandateStatus.DRAFT, "draft_ok") == MandateStatus.PENDING_CONFIRM


def test_draft_to_pending_confirm_rejected_when_bounds_missing() -> None:
    ctx = TransitionGuardContext(all_required_bounds_present=False)
    with pytest.raises(GuardRejected):
        transition(MandateStatus.DRAFT, "draft_ok", ctx)


def test_pending_confirm_to_locked() -> None:
    assert transition(MandateStatus.PENDING_CONFIRM, "confirm") == MandateStatus.LOCKED


def test_pending_confirm_to_locked_rejected_without_matching_draft_hash() -> None:
    ctx = TransitionGuardContext(confirmation_matches_draft_hash=False)
    with pytest.raises(GuardRejected):
        transition(MandateStatus.PENDING_CONFIRM, "confirm", ctx)


def test_locked_to_executing() -> None:
    ctx = TransitionGuardContext(now=NOW, expires_at=LATER, txn_count=0, max_transactions=1)
    assert transition(MandateStatus.LOCKED, "propose", ctx) == MandateStatus.EXECUTING


def test_locked_to_executing_rejected_when_expired() -> None:
    ctx = TransitionGuardContext(now=LATER, expires_at=NOW, txn_count=0, max_transactions=1)
    with pytest.raises(GuardRejected):
        transition(MandateStatus.LOCKED, "propose", ctx)


def test_locked_to_executing_rejected_when_txn_count_exhausted() -> None:
    ctx = TransitionGuardContext(now=NOW, expires_at=LATER, txn_count=1, max_transactions=1)
    with pytest.raises(GuardRejected):
        transition(MandateStatus.LOCKED, "propose", ctx)


def test_executing_to_settled() -> None:
    ctx = TransitionGuardContext(reserved_minor=840000, captured_minor=840000)
    assert transition(MandateStatus.EXECUTING, "captured", ctx) == MandateStatus.SETTLED


def test_executing_to_settled_rejected_on_amount_mismatch() -> None:
    ctx = TransitionGuardContext(reserved_minor=840000, captured_minor=1)
    with pytest.raises(GuardRejected):
        transition(MandateStatus.EXECUTING, "captured", ctx)


def test_executing_to_compensated() -> None:
    ctx = TransitionGuardContext(all_compensations_confirmed=True)
    assert transition(MandateStatus.EXECUTING, "failure", ctx) == MandateStatus.COMPENSATED


def test_executing_to_compensated_rejected_when_unconfirmed() -> None:
    ctx = TransitionGuardContext(all_compensations_confirmed=False)
    with pytest.raises(GuardRejected):
        transition(MandateStatus.EXECUTING, "failure", ctx)


def test_pending_confirm_expires_on_ttl() -> None:
    assert transition(MandateStatus.PENDING_CONFIRM, "ttl") == MandateStatus.EXPIRED


@pytest.mark.parametrize(
    "status",
    [
        MandateStatus.DRAFT,
        MandateStatus.PENDING_CONFIRM,
        MandateStatus.LOCKED,
        MandateStatus.EXECUTING,
    ],
)
def test_revoke_always_accepted(status: MandateStatus) -> None:
    """I-M3: revocation is always accepted; it can only narrow authority."""
    assert transition(status, "revoke") == MandateStatus.REVOKED


def test_revoked_is_a_sink() -> None:
    with pytest.raises(InvalidTransition):
        transition(MandateStatus.REVOKED, "propose")


def test_undefined_trigger_raises_invalid_transition() -> None:
    with pytest.raises(InvalidTransition):
        transition(MandateStatus.DRAFT, "propose")


def test_invalid_transition_error_carries_context() -> None:
    with pytest.raises(InvalidTransition) as exc_info:
        transition(MandateStatus.SETTLED, "confirm")
    assert exc_info.value.current == MandateStatus.SETTLED
    assert exc_info.value.trigger == "confirm"
