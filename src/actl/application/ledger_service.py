"""§12: ledger reservations -- row-locked reserve, capture, release, sweep.

Domain math (account naming, movement construction, balance netting) lives
in `domain.ledger.model`, pure and I/O-free; this module is the only place
that touches the database for it -- same split as `domain.audit` (pure hash
math) vs `application/audit_service.py` (the transactional append).

Every operation here takes the mandate row lock (`SELECT ... FOR UPDATE`,
§12.1) *first*, unconditionally -- including on an idempotent replay -- so
that a second, concurrent call for the same `ref_id` can never race past the
idempotency check before the first call's insert becomes visible. This is
what makes G4 (§11) safe to run on every gate call, replay or not, without
ever double-reserving or leaking a reservation. None of these functions
commit; the caller's transaction (the gate's G1-G5 span, or the saga's own
step) does, exactly once every other check in that span has also passed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from actl.application.audit_service import append_entry
from actl.application.integrity import raise_if_halted
from actl.domain.audit.events import AuditAction
from actl.domain.ledger.model import (
    LedgerMovement,
    ReservationState,
    account,
    capture_movements,
    net_balance,
    release_movements,
    reserve_movements,
    reverse_movements,
)
from actl.infrastructure.db.repositories.ledger_entries import LedgerEntryRecord
from actl.infrastructure.db.uow import UnitOfWork
from actl.platform import tracing
from actl.platform.clock import Clock
from actl.platform.ids import new_id


@dataclass(frozen=True)
class Reservation:
    ref_id: str
    mandate_id: str
    amount_minor: int


def _mandate_id_from_account(acct: str) -> str:
    # "mandate:{mandate_id}:{bucket}" -- mandate ids are ULID-based (no
    # colons), so this split is unambiguous.
    return acct.removeprefix("mandate:").split(":", 1)[0]


def _state_of(entries: list[LedgerEntryRecord]) -> ReservationState | None:
    ref_types = {e.ref_type for e in entries}
    if "expire" in ref_types:
        return ReservationState.EXPIRED
    if "release" in ref_types:
        return ReservationState.RELEASED
    if "capture" in ref_types:
        return ReservationState.CAPTURED
    if "reservation" in ref_types:
        return ReservationState.HELD
    return None


async def _add_movements(
    uow: UnitOfWork,
    movements: tuple[LedgerMovement, LedgerMovement],
    *,
    ref_type: str,
    ref_id: str,
    clock: Clock,
) -> None:
    for movement in movements:
        await uow.ledger_entries.add(
            LedgerEntryRecord(
                account=movement.account,
                direction=movement.direction,
                amount_minor=movement.amount_minor,
                ref_type=ref_type,
                ref_id=ref_id,
                created_at=clock.now(),
            )
        )


async def committed_total(uow: UnitOfWork, mandate_id: str) -> int:
    """§28 P7: reserved + settled, combined -- the same total gate G4's own
    `held + spent` check enforces atomically. Used by the merchant-agent's
    order.propose handler to populate `PolicyContext.reserved_minor` for
    the *advisory* policy-engine pre-check (§10) before the saga's later,
    row-locked reservation makes the real, atomic decision -- so this is a
    plain, non-locking read, not a second source of truth."""
    reserved_entries = await uow.ledger_entries.list_for_account(account(mandate_id, "reserved"))
    settled_entries = await uow.ledger_entries.list_for_account(account(mandate_id, "settled"))
    held = net_balance([(e.direction, e.amount_minor) for e in reserved_entries])
    spent = net_balance([(e.direction, e.amount_minor) for e in settled_entries])
    return held + spent


async def reserve(
    uow: UnitOfWork,
    clock: Clock,
    *,
    mandate_id: str,
    amount_minor: int,
    max_total_minor: int,
    ref_id: str,
) -> Reservation | None:
    """§12.1 G4: row-locked check-and-insert. Returns None if admitting
    this reservation would push `held + spent + amount_minor` over
    `max_total_minor` (-> caller denies BUDGET_EXCEEDED), or if the
    mandate row doesn't exist. Idempotent by `ref_id`: a replay returns the
    existing reservation without re-checking the cap or inserting again --
    I-M4 (§9.2) holds even under a retried gate call."""
    if await uow.mandates.get_for_update(mandate_id) is None:
        return None

    existing = await uow.ledger_entries.list_for_ref_id(ref_id)
    if existing:
        return Reservation(ref_id=ref_id, mandate_id=mandate_id, amount_minor=amount_minor)

    reserved_entries = await uow.ledger_entries.list_for_account(account(mandate_id, "reserved"))
    settled_entries = await uow.ledger_entries.list_for_account(account(mandate_id, "settled"))
    held = net_balance([(e.direction, e.amount_minor) for e in reserved_entries])
    spent = net_balance([(e.direction, e.amount_minor) for e in settled_entries])
    if held + spent + amount_minor > max_total_minor:
        return None

    await _add_movements(
        uow,
        reserve_movements(mandate_id, amount_minor),
        ref_type="reservation",
        ref_id=ref_id,
        clock=clock,
    )
    return Reservation(ref_id=ref_id, mandate_id=mandate_id, amount_minor=amount_minor)


async def capture(
    uow: UnitOfWork, clock: Clock, *, mandate_id: str, amount_minor: int, ref_id: str
) -> bool:
    """S5 SETTLE's ledger half: moves `ref_id`'s reservation reserved ->
    settled. Idempotent: replaying an already-captured ref_id is a no-op
    returning True. Returns False if there is no HELD reservation for
    `ref_id` to capture (already released/expired/never reserved) --
    callers must treat False as a logic error, never retry blindly."""
    if await uow.mandates.get_for_update(mandate_id) is None:
        return False
    state = _state_of(await uow.ledger_entries.list_for_ref_id(ref_id))
    if state is ReservationState.CAPTURED:
        return True
    if state is not ReservationState.HELD:
        return False
    await _add_movements(
        uow,
        capture_movements(mandate_id, amount_minor),
        ref_type="capture",
        ref_id=ref_id,
        clock=clock,
    )
    return True


async def reverse_settlement(
    uow: UnitOfWork, clock: Clock, *, mandate_id: str, amount_minor: int, ref_id: str
) -> bool:
    """C5 REVERSE: a settled capture that must be undone (after a C4
    refund) moves settled -> available via a contra-entry -- never a
    delete (§12.1). Idempotent: replaying an already-reversed ref_id is a
    no-op returning True. Returns False if there is no CAPTURED
    settlement for `ref_id` to reverse."""
    if await uow.mandates.get_for_update(mandate_id) is None:
        return False
    entries = await uow.ledger_entries.list_for_ref_id(ref_id)
    ref_types = {e.ref_type for e in entries}
    if "reverse" in ref_types:
        return True
    if "capture" not in ref_types:
        return False
    await _add_movements(
        uow,
        reverse_movements(mandate_id, amount_minor),
        ref_type="reverse",
        ref_id=ref_id,
        clock=clock,
    )
    return True


async def release(
    uow: UnitOfWork, clock: Clock, *, mandate_id: str, amount_minor: int, ref_id: str
) -> bool:
    """C1 RELEASE: moves `ref_id`'s reservation reserved -> available.
    Idempotent: replaying an already-released ref_id is a no-op returning
    True. Returns False if there is nothing HELD to release."""
    if await uow.mandates.get_for_update(mandate_id) is None:
        return False
    state = _state_of(await uow.ledger_entries.list_for_ref_id(ref_id))
    if state is ReservationState.RELEASED:
        return True
    if state is not ReservationState.HELD:
        return False
    await _add_movements(
        uow,
        release_movements(mandate_id, amount_minor),
        ref_type="release",
        ref_id=ref_id,
        clock=clock,
    )
    return True


async def sweep(uow: UnitOfWork, clock: Clock, *, reservation_ttl_s: int) -> list[str]:
    """§12.2: any HELD reservation older than `reservation_ttl_s` is
    force-released, and a `reservation.expired` audit entry names the
    ref/mandate so the cause is traceable rather than a mysteriously
    shrunk budget. Returns the swept ref_ids. Idempotent/restart-safe: a
    reservation already captured/released/expired by the time this scans
    it (or by the time it re-checks under the row lock) is silently
    skipped, never double-released.

    §20 F10 / §28 P9 instruction 2: refuses to run at all while the
    durable integrity halt is tripped -- a "scheduled/sweep entry point"
    is money-affecting work exactly like any other, so it raises
    `IntegrityHalted` rather than silently releasing reservations while
    the audit trail that would record the release is untrusted."""
    await raise_if_halted(uow)
    cutoff = clock.now() - timedelta(seconds=reservation_ttl_s)
    swept: list[str] = []
    with tracing.span("worker.sweep"):
        for ref_id in await uow.ledger_entries.list_reservations_older_than(cutoff):
            entries = await uow.ledger_entries.list_for_ref_id(ref_id)
            if _state_of(entries) is not ReservationState.HELD:
                continue
            reservation_entry = next(
                e for e in entries if e.ref_type == "reservation" and e.direction == "debit"
            )
            mandate_id = _mandate_id_from_account(reservation_entry.account)
            amount_minor = reservation_entry.amount_minor

            if await uow.mandates.get_for_update(mandate_id) is None:
                continue
            # Re-check under the lock: another writer may have captured or
            # released this ref_id since the unlocked scan above.
            entries = await uow.ledger_entries.list_for_ref_id(ref_id)
            if _state_of(entries) is not ReservationState.HELD:
                continue

            # Each expired reservation is its own transaction -- same "one
            # trace_id per independent worker event" reasoning as
            # payment_service's webhook/reconciliation ticks.
            trace_id = new_id("trc")
            with tracing.transaction_span("worker.sweep_reservation", trace_id, ref_id=ref_id):
                await _add_movements(
                    uow,
                    release_movements(mandate_id, amount_minor),
                    ref_type="expire",
                    ref_id=ref_id,
                    clock=clock,
                )
                await append_entry(
                    uow,
                    trace_id=trace_id,
                    actor_type="system",
                    actor_id="sweeper",
                    action=AuditAction.RESERVATION_EXPIRED,
                    subject={"mandate_id": mandate_id, "ref_id": ref_id},
                    payload={
                        "mandate_id": mandate_id,
                        "ref_id": ref_id,
                        "amount_minor": amount_minor,
                    },
                )
            swept.append(ref_id)
        return swept
