"""§12.1 pure ledger math: account naming, movement construction, and
balance netting. No I/O -- the row-locked read/insert is covered
end-to-end by tests/integration/gate and tests/concurrency instead."""

from __future__ import annotations

from actl.domain.ledger.model import (
    account,
    capture_movements,
    net_balance,
    release_movements,
    reserve_movements,
    reverse_movements,
)


def test_account_naming() -> None:
    assert account("mdt_1", "available") == "mandate:mdt_1:available"
    assert account("mdt_1", "reserved") == "mandate:mdt_1:reserved"
    assert account("mdt_1", "settled") == "mandate:mdt_1:settled"


def test_reserve_movements_sum_to_zero_net_and_move_available_to_reserved() -> None:
    available, reserved = reserve_movements("mdt_1", 500)
    assert available.account == "mandate:mdt_1:available"
    assert available.direction == "credit"
    assert reserved.account == "mandate:mdt_1:reserved"
    assert reserved.direction == "debit"
    assert available.amount_minor == reserved.amount_minor == 500
    # available decreases (credit), reserved increases (debit) -- net zero
    assert net_balance([(available.direction, available.amount_minor)]) == -500
    assert net_balance([(reserved.direction, reserved.amount_minor)]) == 500


def test_capture_movements_move_reserved_to_settled() -> None:
    reserved, settled = capture_movements("mdt_1", 500)
    assert reserved.account == "mandate:mdt_1:reserved"
    assert reserved.direction == "credit"
    assert settled.account == "mandate:mdt_1:settled"
    assert settled.direction == "debit"


def test_release_movements_move_reserved_to_available() -> None:
    reserved, available = release_movements("mdt_1", 500)
    assert reserved.account == "mandate:mdt_1:reserved"
    assert reserved.direction == "credit"
    assert available.account == "mandate:mdt_1:available"
    assert available.direction == "debit"


def test_reverse_movements_move_settled_to_available() -> None:
    settled, available = reverse_movements("mdt_1", 500)
    assert settled.account == "mandate:mdt_1:settled"
    assert settled.direction == "credit"
    assert available.account == "mandate:mdt_1:available"
    assert available.direction == "debit"


def test_reserve_then_release_nets_back_to_zero() -> None:
    """A reservation followed by its release must leave the reserved
    bucket's net balance at exactly zero -- otherwise a released
    reservation would silently leak budget (§12.2)."""
    _, reserved_debit = reserve_movements("mdt_1", 300)  # reserved += 300
    reserved_credit, _ = release_movements("mdt_1", 300)  # reserved -= 300
    net = net_balance(
        [
            (reserved_debit.direction, reserved_debit.amount_minor),
            (reserved_credit.direction, reserved_credit.amount_minor),
        ]
    )
    assert net == 0


def test_net_balance_empty_is_zero() -> None:
    assert net_balance([]) == 0
