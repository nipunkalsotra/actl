# 0014 — Ledger reservations over balance checks

Status: Accepted
Date: 2026-08-30

## Context

Every money action has to respect a mandate's spending bounds
(`max_total_minor`, `max_unit_minor`, `max_transactions`, §9) even under
concurrent requests against the same mandate. The simplest possible
design reads the mandate's current committed spend, checks it against
the bound, and proceeds if there's room — but that check-then-act
sequence has a well-known race: two concurrent requests can both read
"room for this amount" before either commits, and both proceed,
over-spending the mandate. The architecture has to decide how budget
enforcement stays correct under real concurrency, not just under a
single-threaded test.

## Decision

Spending capacity is tracked as double-entry ledger movements (§12) with
an explicit `reserved` state between "budget checked" and "payment
captured," not a plain balance-and-check. G4 (`ledger_reserve`,
§11.1) takes a `SELECT ... FOR UPDATE` row lock on the mandate *before*
computing whether the new reservation fits inside `max_total_minor`
(§12.1: "every operation here takes the mandate row lock ... first,
unconditionally") — so a second concurrent request for the same mandate
cannot even read the current reserved total until the first request's
reservation (or its rollback) is already durable. A reservation is
released (§12.2, C1) if the downstream provider call fails, captured
(moved `reserved` → `settled`) on success, or expired by
`ledger_service.sweep` if left `HELD` past `reservation_ttl_s` with no
resolution — never silently left in a state that either double-counts or
under-counts spend.

## Consequences

- **Concurrent over-admission is structurally impossible, not merely
  unlikely.** `tests/integration/gate/test_gate_concurrency.py` fires
  many simultaneous requests against one mandate and asserts the
  reserved total never exceeds the bound — the row lock, not application
  logic discipline, is what makes this true.
- **A reservation has an explicit lifecycle**, so "how much is actually
  at risk right now" is always a real, queryable ledger state
  (`ledger_service.committed_total`), not something reconstructed after
  the fact from scattered order rows — which is also what makes
  `budget.reserved` a meaningful, independently auditable line in the
  chain (§16.3), not just an internal accounting detail.
- **A crashed process between reservation and resolution leaves a
  recoverable `HELD` row, not a silent leak** — `ledger_service.sweep`
  (a scheduled/worker entry point, itself refusing to run under an
  integrity halt, §20 F10) force-releases anything left `HELD` past its
  TTL, with its own `reservation.expired` audit entry naming the cause.

## Alternatives considered

- **A plain running-balance check** (read current spend, compare against
  the cap, write the new spend). Rejected: this is exactly the
  check-then-act race described above; making it safe under concurrency
  would require the same row-locking discipline this design already has,
  just without the explicit reserved/settled states that make partial
  failure recoverable.
- **Optimistic concurrency (a version column, retry on conflict) instead
  of a row lock.** Rejected: G4 already sits inside a retry-on-deadlock
  loop for the rarer G4/G7 lock-ordering conflict (§11 DESIGN RULE
  comment, `gate.py`); a *second* independent optimistic-retry layer for
  the common case (many concurrent requests against one popular mandate)
  would add complexity for a race the row lock already closes for free.

## Relevant architecture section

§9 Mandate subsystem — spending bounds; §12 Ledger — reservations,
capture, release, sweep; §11.1 Money Action Gate — G4 budget reservation.
