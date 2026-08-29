"""§20 F10: durable, cross-process integrity halt. State lives in Postgres
(`integrity_halt`, migrations/versions/0007_integrity_halt.py) -- the same
durable system of record §18.1 already establishes for every other piece
of state this build has -- never in process-local Python memory. Every
process reads the *same* database row on every money-affecting action, so
a halt tripped by one process/instance is honored immediately by every
other one sharing that database, including a process that has never
handled a single request before the row was tripped (proven by `tests/
chaos/test_f10.py`'s genuinely-separate-OS-process test). This replaces
the earlier P9 in-memory `IntegrityHalt` singleton this same module used
to define, which gave no such guarantee -- see docs/adr/0010 decision 16.

There is no `clear()`/reset function anywhere in this module, in
`infrastructure.db.repositories.integrity.IntegrityHaltRepository`, or
anywhere else in application code. §20 gives F10 no automated recovery
path -- its response is "halt all money actions, raise alarm, refuse to
proceed," naming no compensating or reset action, unlike every other
failure mode's own row in that table. Clearing a halt is therefore a
deliberate, manual, direct-database operation an operator performs after
off-band forensic investigation, documented in docs/runbook.md -- never a
code path that could fire by accident, automation, or a future refactor.
"""

from __future__ import annotations

from actl.infrastructure.db.uow import UnitOfWork


class IntegrityHalted(Exception):
    """Raised by any money-affecting application function that finds the
    system durably halted. `reason` is the durably-stored trip reason
    (§20 F10's own "raise alarm")."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"integrity halt active: {reason}")
        self.reason = reason


async def raise_if_halted(uow: UnitOfWork) -> None:
    """One shared check for every money-affecting entry point that isn't
    `application.gate.execute_money_action` itself (which has its own
    typed-`MoneyActionResult`, never-raises convention and checks the
    same durable state inline) -- `application.ledger_service.sweep`,
    `application.payment_service.process_unprocessed_webhooks`, and
    `reconcile_non_terminal_orders` (§28 P9 instruction 2: "API, worker,
    demo, and scheduled/sweep entry points must all refuse money-
    affecting work while the halt is active")."""
    state = await uow.integrity.get_state()
    if state.halted:
        raise IntegrityHalted(state.reason or "unknown")
