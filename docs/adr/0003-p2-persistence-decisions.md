# 0003 — P2 persistence decisions

Status: Accepted
Date: 2026-08-28

## Context

P2 ("Persistence — schema, repositories, outbox", §28) implements §18.2's
schema, async repositories, and a UnitOfWork. §18.2 is explicitly marked
"(excerpt)" for both migration files, and P1 built only two domain models
(`Mandate`, `DecisionRecord`) — the phases that own the rest of the domain
model (catalog/quotes P4, order+payment P6, ledger P6, audit chain P3,
agent identities P7) haven't run yet. Several material decisions filled
those gaps; recorded here per §28's standing instruction. None weakens a
mandatory security, payment, audit, idempotency, failure-handling, or test
requirement — each is either a necessary completion of what §18.2's own
excerpt demands, or a documented scope boundary.

## Decisions

### 1. `quotes` table added — not in §18.2's excerpt, required by orders' own FK

`orders.quote_id TEXT NOT NULL REFERENCES quotes(id)` is given verbatim in
§18.2, but no `CREATE TABLE quotes` appears in the excerpt. Its columns are
exactly the Quote v1 fields from §8.4 (`quote_id`, `sku`, `mandate_id`,
`unit_price_minor`, `nights`, `total_minor`, `currency`, `catalog_version`,
`refundable`, `expires_at`, `quote_token`, `quote_hash`) — nothing added
beyond that set. Without this table, orders' own specified FK is
unsatisfiable, so this is a completion of §18.2, not an addition outside it.

### 2. `payments` repository maps onto the `orders` table — no separate `payments` table exists

§18.2 has one `orders` table carrying `status` (`CREATED|AUTHORIZED|
CAPTURED|FAILED|COMPENSATED`), `provider_order_id`, and `idempotency_key` —
the full payment lifecycle already lives there. §6.2's module register
lists "order: Order **and payment** aggregates" as one module. `payments.py`
is a payment-shaped view (`PaymentRepository`) over the same `OrderRow`
`orders.py` writes, not a second table.

### 3. Local infrastructure-only record types for tables with no P1 domain model yet

P1's explicit scope was "mandate, canonical JSON, policy engine" — it did
not build `domain/catalog/`, `domain/order/`, `domain/ledger/`, or
`domain/audit/`'s entry model (only `canonical.py`). Eight of the ten
requested repositories (quotes, orders, payments, ledger_entries,
audit_log, outbox, webhook_events, idempotency_keys) therefore have no pure
domain model to map to/from yet. Each repository module defines a minimal,
frozen local dataclass (`QuoteRecord`, `OrderRecord`, `LedgerEntryRecord`,
`AuditLogRecord`, `OutboxRecord`, `WebhookEventRecord`,
`IdempotencyKeyRecord`) representing exactly its persisted row shape —
outside `actl.domain`, so the import-linter "Domain is pure" contract is
unaffected either way. Redirect each repository to a real domain model once
the phase that owns it (P3 audit chain, P4 catalog, P6 ledger/order) adds
one; only `mandates.py` and `decisions.py` map to real P1 domain models
(`Mandate`, `DecisionRecord`) today.

### 4. ~~Append-only trigger is scoped to `audit_log` only, not `ledger_entries`~~ — SUPERSEDED by decision 10

Original reasoning (kept for the record): §18.2's own excerpt comments
`ledger_entries` as "-- append-only, double entry" but shows trigger DDL
only for `audit_log`, and the phase's Deliverables bullet says "the
append-only trigger" (singular). On a targeted re-read of §12.1 ("Ledger
and budget reservations") for a security review, that section states
unambiguously: "Append-only ledger_entries. Corrections are contra-entries;
rows are never updated or deleted." — an explicit requirement §18.2's
comment alone didn't make obvious. Decision 10 adds the trigger this
section requires.

### 5. `mandates.spec` holds the complete mandate JSON; other columns are denormalized extracts

§18.2 comments `spec JSONB NOT NULL -- the full v1 object`. `MandateRepository`
treats this literally: `to_domain(row)` is exactly
`Mandate.model_validate(row.spec)`, and `spec_hash`/`signature`/`currency`/
the cap columns are populated from the same `Mandate` object purely for
indexing and the `locked_has_hash` CHECK — never a second source of truth
read back on load. This also means `signature`'s `alg`/`key_id` sub-fields
(dropped by the flat `signature TEXT` column, per §18.2) round-trip
correctly anyway, since they're reconstructed from `spec`, not from that
flat column.

### 6. ~~The `audit_log` narration WHEN-clause permits a smuggled column change alongside narration~~ — SUPERSEDED by decision 10

Original finding (kept for the record): §18.2's literal trigger text —
`FOR EACH ROW WHEN (OLD.narration IS NOT DISTINCT FROM NEW.narration)` —
fires only when narration is *unchanged*, so an UPDATE that changed
narration *and* payload in the same statement was not blocked (verified
directly against Postgres: `UPDATE audit_log SET narration='n1',
payload='{"x":1}' ...` → `UPDATE 1`, succeeded). This was flagged as a gap
worth the architecture owner's attention rather than silently fixed, per
this phase's instruction not to deviate from §18.2's given DDL without
saying so. The owner asked for the fix; decision 10 closes it.

### 7. Migration head after P2 is `0002`, not the exit criteria's illustrative `0001 -> 0002 -> 0003`

§28 P2's exit criteria shows `alembic: 0001 -> 0002 -> 0003 (head)`. This
phase's actual scope is "every table in §18.2" only; `0003_agent_identities`
(§25) covers agent identity tables that are not in §18.2 and belong to P7
(agent protocol). Chained onto P0's foundation revision (`0000`), P2 ships
exactly `0001_core` and `0002_audit_outbox`; head is `0002`. Consistent
with ADR 0001 decision 3's reasoning for why P0's own migration state
didn't match that same illustrative text either.

### 8. `UnitOfWork` implemented concretely only — no `application/unit_of_work.py` port yet

§25's tree lists both `application/unit_of_work.py` and
`infrastructure/db/uow.py`, suggesting an eventual port/adapter split. At
P2 there is exactly one implementation (Postgres-backed) and no consumer
yet needing to be decoupled from it — `application/gate.py` (P6) is the
first phase with a real caller. Introducing an abstract port now, with one
implementation and zero current consumers, would be exactly the
unrequested abstraction Ponytail's ladder says to skip. `UnitOfWork` lives
in `infrastructure/db/uow.py` only; add the `application` port when P6
actually needs to inject or mock it.

### 9. testcontainers' Ryuk cleanup sidecar disabled (`TESTCONTAINERS_RYUK_DISABLED=true`)

This machine's Docker Desktop doesn't share its socket path with the Ryuk
sidecar container testcontainers normally uses for automatic orphan
cleanup (`mounts denied` on container start — a local Docker Desktop
config issue, not a code or environment-portability problem). The
integration suite stops its Postgres container explicitly via the `with
PostgresContainer(...)` context manager regardless, so disabling Ryuk costs
only automatic cleanup of containers orphaned by a killed test process, not
any test guarantee. The real database, its triggers, and its constraints
are unaffected — this is a cleanup-sidecar toggle, not a test substitution.

### 10. Security correction: tightened `audit_log` UPDATE guard, added strict `ledger_entries` trigger

Requested as a narrowly-scoped, security-preserving correction to decisions
4 and 6 above — not a new feature and not a weakening of anything §18.2
requires.

**`audit_log`** (`migrations/versions/0002_audit_outbox.py`): the
`audit_log_no_update` trigger's WHEN clause changed from comparing
narration alone to comparing the *entire row minus narration*:
```sql
WHEN ((to_jsonb(OLD) - 'narration') IS DISTINCT FROM (to_jsonb(NEW) - 'narration'))
```
An UPDATE now succeeds only when every column except narration is
unchanged — including when narration is touched in the same statement as a
protected column, which is exactly the gap decision 6 identified. Function
name, trigger names, `audit_log_no_delete`, and the exception message
format are all unchanged from the original §18.2 DDL; only the UPDATE
guard's condition was corrected. Verified directly against Postgres:
narration-only UPDATE succeeds; UPDATE of any other column alone fails;
narration + another column together now also fails; DELETE fails.

**`ledger_entries`** (`migrations/versions/0001_core.py`): a new
`ledger_entries_immutable()` function plus `ledger_entries_no_update` /
`ledger_entries_no_delete` triggers, unconditional — no WHEN clause, no
carve-out, matching §12.1's "rows are never updated or deleted" exactly
(unlike `audit_log`, no column is described as an exception). INSERT is
untouched; corrections must be contra-entries per §12.1, which is an
application-layer convention this trigger makes structurally impossible to
bypass at the database layer.

Both changes are edits to the existing P2 migrations, not new migration
revisions — P2 has not been committed, so there is no shipped revision
history to preserve by appending a fix-up migration instead.

## Consequences

- P3 (trust layer) must reconcile its own `domain/audit/chain.py`
  hash-chaining logic with `AuditLogRepository`'s current signature; the
  narration WHEN-clause gap (decisions 4/6) is closed by decision 10, so P3
  inherits the tightened trigger rather than needing to fix it itself.
- P4 (catalog) should redirect `QuoteRepository` to a real
  `actl.domain.catalog.Quote` model when it lands, retiring `QuoteRecord`.
- P6 (Money Action Gate, ledger) should redirect `OrderRepository` /
  `PaymentRepository` / `LedgerEntryRepository` to real domain models from
  `domain/order/` and `domain/ledger/`, and is the right phase to add the
  `application/unit_of_work.py` port per decision 8.
- P7 (agent protocol) owns `0003_agent_identities` and should chain it onto
  `down_revision = "0002"`.
