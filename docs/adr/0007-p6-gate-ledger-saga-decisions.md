# 0007 — P6 Money Action Gate, ledger, and saga decisions

Status: Accepted
Date: 2026-08-29

## Context

P6 ("Money Action Gate, ledger and saga", §28) implements §11 (the seven
gates), §12 (ledger reservations), and §15's saga (S1-S5 forward, C1-C5
compensation). This is the phase the architecture itself calls "the
centrepiece" and instructs to build with zero simplification. Several
places where §11/§12/§15 state a principle without pinning down the exact
mechanics required an interpretive decision; two required deviating from
the literal reference pseudocode to keep the stated guarantees actually
true under concurrency. Both classes are recorded here, per this
project's standing practice (see ADR 0006 for the P5 precedent).

## Decisions

### 1. Ledger balances are netted by signed direction, not summed unconditionally

§12.1's example SQL computes `held`/`spent` via
`SELECT COALESCE(SUM(amount_minor),0) ... WHERE account = 'mandate:...:reserved'`
— summing every row in the account regardless of `direction`. That is
correct for the *first ever* reservation against a fresh mandate, but it
is not a correct definition of "balance": once a reservation is released
or captured, a second entry is posted into the same `reserved` account,
and unconditionally summing `amount_minor` would count both the original
hold and its release as positive, never netting back to zero. That
directly breaks §12.2 ("a leaked reservation silently shrinks a
mandate's usable budget") the moment any reservation is ever released.

`domain/ledger/model.py` instead defines every movement with an explicit
sign convention (`debit` increases a bucket's net balance, `credit`
decreases it) and `application/ledger_service.py` computes `held`/`spent`
as `net_balance()` — `SUM(debit) - SUM(credit)`. A reservation posts
`available: credit`, `reserved: debit`; a release posts `reserved:
credit`, `available: debit` — the second pair exactly cancels the
first's `reserved` entry under netting. `tests/unit/domain/ledger/
test_model.py::test_reserve_then_release_nets_back_to_zero` is the direct
proof. This is "balances are derived by summation" (§12.1) read as
summation of *signed* amounts, which is what makes it a balance at all.

### 2. Reservations are idempotent by `ref_id`, keyed to the same idempotency key `payment_service.py` already derives

§11.2's reference pseudocode runs G4 (reserve) unconditionally on every
call, *before* G6's idempotency check — taken literally, a replayed
attempt (the same `(mandate_id, intent_hash, attempt_no)` resubmitted,
e.g. after a client-side timeout) would take a *second* reservation for
money already reserved by the first attempt, silently doubling the hold.

`ledger_service.reserve/capture/release` are idempotent by `ref_id`: the
gate passes `ref_id = compute_idempotency_key(mandate_id, intent_hash,
attempt_no)` (§15.2's existing formula — no second id scheme invented).
A second call with the same `ref_id` finds the existing reservation and
returns it without re-checking the cap or inserting again. This
preserves the letter of "G4 must precede EXECUTE, always" (§11 DESIGN
RULE) while making it safe to actually run that way. The mandate row
lock (`SELECT ... FOR UPDATE`) is still acquired unconditionally on every
call, including idempotent replays, so the idempotency check itself is
race-free under true concurrency on the same `ref_id`.

### 3. G1 admits `LOCKED` *and* `EXECUTING`, not `LOCKED` only

A mandate's first successful gate call transitions it `LOCKED ->
EXECUTING` (§9.1) before G6/G7/EXECUTE ever run. A literal `status ==
LOCKED` check in G1 would then reject the *replay itself* — the second
call for the same `(mandate, intent, attempt_no)` sees the mandate is no
longer LOCKED and denies `MANDATE_INVALID`, even though G6's idempotent
replay is exactly what should happen instead. It would equally reject a
*second, distinct* attempt against a mandate whose `max_transactions >
1` (rule `cap.count`, §10.1, exists specifically to bound how many such
attempts are allowed — a check that can only ever fire if a mandate is
allowed to attempt more than once while `EXECUTING`).

G1 therefore admits both `LOCKED` and `EXECUTING`; the `LOCKED ->
EXECUTING` transition itself only fires once (guarded by `if status is
MandateStatus.LOCKED`), so a replay or a later attempt leaves the status
untouched. A terminal status (`SETTLED`, `COMPENSATED`, `REVOKED`, ...)
is still rejected. `test_gate_g1_allows_a_second_attempt_against_an_
already_executing_mandate` and `test_gate_g1_rejects_a_settled_mandate`
cover both sides.

### 4. G6+G7+EXECUTE are delegated to `payment_service.create_provider_order`, not reimplemented in `gate.py`

§11.2's reference implementation inlines the idempotency claim, the
write-ahead audit entry, and the `provider.create_order` call directly
inside `execute_money_action`. `application/payment_service.py` (§28 P5)
already implements exactly that sequence — including the two-phase
commit boundary a slow provider call requires, the bounded poll for a
lost idempotency-claim race, and the retry/circuit-breaker composition —
and is already fully tested. Re-implementing it inline in `gate.py` would
either duplicate that logic or regress one of its already-fixed
concurrency bugs (ADR 0006 decisions 9-10). `gate.py`'s G1-G5 instead run
in one transaction (committing only once every check passes), then hand
off to `create_provider_order` for G6/G7/EXECUTE. `gate.py` never imports
the concrete Razorpay adapter either way — like `payment_service.py`, it
receives the injected `PaymentProvider` port.

### 5. Deadlock-retry around G1-G5's transaction and around `create_provider_order`

Under heavy same-mandate concurrency (the 50-parallel exit-criteria
test), the mandate row lock (G4, per-mandate) and the audit chain's
single global advisory lock (`acquire_chain_lock`, taken inside G7's
write-ahead entry and again inside `create_provider_order`'s own
write-ahead entry) can form a genuine Postgres deadlock cycle: many
concurrent transactions each hold one of the two locks while waiting for
the other, in different relative orders depending on scheduling. This is
not a bug in lock *ordering* within one code path — every call acquires
the row lock before the advisory lock, consistently — it is inherent to
having a single global serialization point (the audit chain) contended
by many transactions that are also serialized per-mandate. Postgres
detects the cycle and aborts one participant with `DeadlockDetectedError`.

Both `execute_money_action`'s G1-G5 attempt and its call to
`create_provider_order` are wrapped in `retry_with_full_jitter(...,
retry_on=(DBAPIError,))`. This is safe because a deadlock always aborts
its transaction *before* any commit in that attempt — `_attempt_g1_
through_g5` never partially commits, and `create_provider_order`'s
write-ahead entry lives in its first transaction, entirely before the
provider call — so retrying the whole attempt from a fresh `UnitOfWork`
can never duplicate a reservation, an order row, or an audit entry. This
is the standard, expected mitigation for contention of this shape (real
Postgres applications retry serialization failures routinely); it is not
a workaround for a design flaw the architecture asks to avoid.
`test_gate_g4_no_overspend_under_concurrency` is stable across repeated
runs with this in place.

### 6. A new import-linter "protected" contract, alongside the existing "forbidden" contract

§23.4's fitness test checks a single, precise property: no module other
than the gate imports the concrete Razorpay adapter. The existing
`.importlinter` contract 3 (ADR 0006) is a `forbidden`-type contract
enumerating specific source modules (`actl.interfaces`,
`actl.application.agents`, `actl.application.conversation`, ...) — precise
today, but silently out of scope for any *new* application module added
later that happens to import the adapter. Contract 4 uses import-linter's
native `protected` contract type instead: `protected_modules =
actl.infrastructure.providers.razorpay`, `allowed_importers =
actl.application.gate, actl.infrastructure.providers.factory` (the
factory being infrastructure's own composition root, invoked only from
`actl.main`/`actl.cli`/`actl.worker` — same precedent as ADR 0006
decision 1). This checks the *whole* import graph, so it can never be
silently bypassed by a new module contract 3 doesn't yet name. Both
contracts are kept — contract 4 strengthens contract 3, it does not
replace it (per the P6 instruction not to weaken existing contracts).
`tests/architecture/test_boundaries.py::test_only_gate_imports_payment_
provider` is the pytest-native form of the same check, run by `make
test`; it was verified to fail (real captured output, both this pytest
test and both import-linter contracts) by temporarily adding a violating
import to `payment_service.py`, then removed.

### 7. `mandate_signing_key` added to `Settings`

G1 must independently re-verify `mandate.signature` against a real key
(`domain.mandate.signing.verify_signature` requires one explicitly — it
never reads a secret itself, by design). No P1-P5 phase added a
config-driven mandate signing key; `tests/integration/db/conftest.py`'s
`make_locked_mandate()` fixture signed with a hardcoded test-only
constant instead. `mandate_signing_key` was added to `Settings` (same
test-mode-placeholder spirit as `quote_signing_key`), and the fixture
now signs against `settings.mandate_signing_key` so gate tests exercise
the same key path production code would use. No other P0-P5 test
inspects the signature's cryptographic validity, so this is a safe,
non-breaking change to shared test infrastructure.

### 8. Saga rows are keyed by the same idempotency key, one row per purchase attempt

`sagas.id` reuses `compute_idempotency_key(mandate_id, intent_hash,
attempt_no)` rather than inventing a second id scheme — a saga and its
order share that identity 1:1 by construction. `step`/`status` are
updated in place (not append-only): saga state is a current-state
record, not an event log; the audit trail for its transitions is the
`audit_log` entries each step already writes alongside it (§15
"Durability guarantees").

### 9. Two saga entry points, matching the real checkout-callback split

§15.4: the gate ends at a pending `Order`, not a charge — completing a
sale needs the payer's own, later, out-of-band checkout authorization
(a real browser callback in production, not yet built; §15.4 explicitly
defers Checkout UI past P10). `application/orchestrator/saga.py`
therefore exposes `begin_purchase` (S1 RESERVE + S2 ORDER, restart-safe
by the same saga-row check) and `complete_purchase` (S3 AUTHORIZE + S4
CAPTURE + S5 SETTLE, given the checkout outcome). Automated tests act as
"the browser": they fetch the `SimulatorAdapter`'s recorded payment and
build a checkout signature via its existing test helper
(`build_checkout_payload`, already added in P5 for exactly this future
use), modelling the real callback shape without needing a UI.

S3 AUTHORIZE's decline check reads `provider.fetch_payments` directly
(available synchronously from the simulator, since it is not
UI-driven) rather than waiting on a signature that a declined payment
never produces — matching "the test credential drives success or
decline" (§15's S3 description) precisely.

### 10. Compensation ordering: C2 VOID before C1 RELEASE; C4 REFUND before C5 REVERSE

§15's compensation table pairs each forward step with its reverse (S1↔C1
... S5↔C5) and states compensations run "in strict reverse order." A
failure discovered at S3/S4 (after S2 ORDER succeeded) must therefore
undo S2 before S1: `void_order_and_release_reservation` marks the order
FAILED (C2) *then* releases the reservation (C1), in that order, in one
transaction. Symmetrically, a failure discovered after S4 CAPTURE
succeeded (the ledger unable to record a settlement that already
happened at the provider) undoes S4 before S5's ledger half:
`refund_and_reverse_settlement` calls `provider.refund` (C4) *then*
posts the ledger's settled→available contra-entry (C5).

`refund_and_reverse_settlement`'s order-status guard deliberately
differs from `void_order_and_release_reservation`'s: the latter leaves
an already-terminal order alone (idempotent no-op), but the former's
whole purpose is moving an order *from* the terminal `CAPTURED` status
*to* `FAILED` — so it only treats `FAILED` itself (this exact
compensation, replayed) as the idempotent no-op case, not `CAPTURED`.

### 11. The kill-switch is checked in `complete_purchase`, between S2 and S3

§9.1: "any -> REVOKED ... in-flight saga halted at the next safe point;
reservations released." No revoke endpoint exists yet (§28 places it at
P7+), but the state machine's `REVOKED` status is already fully modelled
(P1). `complete_purchase` checks the mandate's current status before S3;
a `REVOKED` mandate triggers the same C2+C1 compensation a declined
payment would, and — because revocation is monotonic (I-M3, §9.2) —
`_mark_compensated`'s existing `EXECUTING`-only guard means a `REVOKED`
mandate is correctly left `REVOKED`, never overwritten to `COMPENSATED`.

## Consequences

- Every ledger operation (reserve/capture/release/sweep) is idempotent by
  `ref_id` and takes the mandate row lock unconditionally; this is a
  small, constant per-call cost (§12.1's own stated tradeoff) that in
  return makes G4 safe to run on every gate call, replay or not.
- The deadlock-retry wrapping is scoped narrowly (`DBAPIError` only,
  bounded attempts, full jitter) and only around the two spans that
  provably never partially commit; it does not change behaviour on any
  path that does not hit contention, and every P0-P5 test still passes
  unmodified with it in place.
- `gate.py` and `orchestrator/saga.py` both depend only on the
  `PaymentProvider` port; neither imports a concrete adapter. Contract 4
  (`protected`) plus `test_only_gate_imports_payment_provider` are the
  standing, executable proof of that for every module the whole `actl`
  package will ever contain, not just the ones enumerated today.
