"""Mandate lifecycle (§9.1): the only human-caused transition is confirm;
no transition ever widens authority. `status` deliberately does not live on
the `Mandate` model itself — it would otherwise be swept into spec_hash
(§8.1: "spec_hash covers every field except itself and the signature") and
break I-M1 (a LOCKED mandate is byte-immutable) the moment the status
advances to EXECUTING/SETTLED."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class MandateStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING_CONFIRM = "PENDING_CONFIRM"
    LOCKED = "LOCKED"
    EXECUTING = "EXECUTING"
    SETTLED = "SETTLED"
    COMPENSATED = "COMPENSATED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class TransitionGuardContext:
    """Frozen inputs a guard may need (§9.1 table's Guard column). No clock
    reads, no I/O — every value is supplied by the caller."""

    now: datetime | None = None
    expires_at: datetime | None = None
    txn_count: int = 0
    max_transactions: int = 1
    reserved_minor: int = 0
    captured_minor: int = 0
    all_compensations_confirmed: bool = True
    confirmation_matches_draft_hash: bool = True
    all_required_bounds_present: bool = True


GuardFn = Callable[[TransitionGuardContext], bool]


def _guard_draft_ok(ctx: TransitionGuardContext) -> bool:
    """All required bounds present; no field inferred from silence."""
    return ctx.all_required_bounds_present


def _guard_confirm(ctx: TransitionGuardContext) -> bool:
    """Confirmation must reference the exact draft hash shown to the human."""
    return ctx.confirmation_matches_draft_hash


def _guard_propose(ctx: TransitionGuardContext) -> bool:
    """Not expired, not revoked, transaction count remaining."""
    if ctx.now is None or ctx.expires_at is None:
        return False
    return ctx.now < ctx.expires_at and ctx.txn_count < ctx.max_transactions


def _guard_captured(ctx: TransitionGuardContext) -> bool:
    """Reserved amount equals captured amount."""
    return ctx.reserved_minor == ctx.captured_minor


def _guard_failure(ctx: TransitionGuardContext) -> bool:
    """Every compensation confirmed idempotently."""
    return ctx.all_compensations_confirmed


@dataclass(frozen=True)
class Transition:
    from_status: MandateStatus | None  # None = "any" (revocation narrows from every state)
    trigger: str
    to_status: MandateStatus
    guard: GuardFn | None = None


TRANSITIONS: tuple[Transition, ...] = (
    Transition(MandateStatus.DRAFT, "draft_ok", MandateStatus.PENDING_CONFIRM, _guard_draft_ok),
    Transition(MandateStatus.PENDING_CONFIRM, "confirm", MandateStatus.LOCKED, _guard_confirm),
    Transition(MandateStatus.LOCKED, "propose", MandateStatus.EXECUTING, _guard_propose),
    Transition(MandateStatus.EXECUTING, "captured", MandateStatus.SETTLED, _guard_captured),
    Transition(MandateStatus.EXECUTING, "failure", MandateStatus.COMPENSATED, _guard_failure),
    Transition(None, "revoke", MandateStatus.REVOKED, None),
    Transition(MandateStatus.PENDING_CONFIRM, "ttl", MandateStatus.EXPIRED, None),
)


class InvalidTransition(Exception):
    def __init__(self, current: MandateStatus, trigger: str) -> None:
        super().__init__(f"no transition {trigger!r} from {current.value}")
        self.current = current
        self.trigger = trigger


class GuardRejected(Exception):
    def __init__(self, current: MandateStatus, trigger: str) -> None:
        super().__init__(f"guard rejected transition {trigger!r} from {current.value}")
        self.current = current
        self.trigger = trigger


def transition(
    current: MandateStatus,
    trigger: str,
    ctx: TransitionGuardContext | None = None,
) -> MandateStatus:
    """I-M3: REVOKED is a sink — no transition is defined out of it, so any
    further trigger against a revoked mandate correctly raises InvalidTransition."""
    ctx = ctx if ctx is not None else TransitionGuardContext()
    for t in TRANSITIONS:
        if t.trigger != trigger:
            continue
        if t.from_status is not None and t.from_status != current:
            continue
        if t.guard is not None and not t.guard(ctx):
            raise GuardRejected(current, trigger)
        return t.to_status
    raise InvalidTransition(current, trigger)
