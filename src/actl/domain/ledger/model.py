"""Pure ledger math (§12.1, §12.2): account naming, the reservation state
machine, and balance-from-entries computation. No I/O — the row-locked
read/insert lives in `application/ledger_service.py`, same domain/
application split as `domain/audit` (pure hash math) vs `application/
audit_service.py` (the transactional append).
"""

from __future__ import annotations

from enum import StrEnum
from typing import NamedTuple


def account(mandate_id: str, bucket: str) -> str:
    """§12.1: `mandate:{id}:available|reserved|settled`."""
    return f"mandate:{mandate_id}:{bucket}"


class ReservationState(StrEnum):
    """§12.2 reservation lifecycle."""

    HELD = "HELD"
    CAPTURED = "CAPTURED"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"


class LedgerMovement(NamedTuple):
    account: str
    direction: str  # "debit" | "credit"
    amount_minor: int


def reserve_movements(mandate_id: str, amount_minor: int) -> tuple[LedgerMovement, LedgerMovement]:
    """§12.1: "A reservation moves value available -> reserved... every
    movement is two entries that sum to zero." `debit` increases a
    bucket's net balance, `credit` decreases it (the sign convention that
    makes `capture`/`release` correctly net back to zero via SUM)."""
    return (
        LedgerMovement(account(mandate_id, "available"), "credit", amount_minor),
        LedgerMovement(account(mandate_id, "reserved"), "debit", amount_minor),
    )


def capture_movements(mandate_id: str, amount_minor: int) -> tuple[LedgerMovement, LedgerMovement]:
    """A capture moves reserved -> settled."""
    return (
        LedgerMovement(account(mandate_id, "reserved"), "credit", amount_minor),
        LedgerMovement(account(mandate_id, "settled"), "debit", amount_minor),
    )


def release_movements(mandate_id: str, amount_minor: int) -> tuple[LedgerMovement, LedgerMovement]:
    """A compensation (release) moves reserved -> available."""
    return (
        LedgerMovement(account(mandate_id, "reserved"), "credit", amount_minor),
        LedgerMovement(account(mandate_id, "available"), "debit", amount_minor),
    )


def reverse_movements(mandate_id: str, amount_minor: int) -> tuple[LedgerMovement, LedgerMovement]:
    """C5 REVERSE: a settled capture that must be undone (after a refund)
    moves settled -> available. A contra-entry, never a delete (§12.1)."""
    return (
        LedgerMovement(account(mandate_id, "settled"), "credit", amount_minor),
        LedgerMovement(account(mandate_id, "available"), "debit", amount_minor),
    )


def net_balance(entries: list[tuple[str, int]]) -> int:
    """entries: list of (direction, amount_minor). debit adds, credit
    subtracts -- the convention `reserve_movements`/`capture_movements`/
    `release_movements` above are built on."""
    total = 0
    for direction, amount_minor in entries:
        total += amount_minor if direction == "debit" else -amount_minor
    return total
