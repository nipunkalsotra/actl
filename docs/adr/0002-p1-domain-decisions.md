# 0002 — P1 domain decisions

Status: Accepted
Date: 2026-08-28

## Context

P1 ("Domain core — mandate, canonical JSON, policy engine", §28) implements
the pure domain layer against §8 (domain model), §9 (mandate subsystem) and
§10 (policy engine). Those sections fully specify the Mandate, DecisionRecord
and AuditEntry schemas and the twelve-rule table, but they do not specify
everything the code needs to compile and run. Six material decisions filled
those gaps. Per §28's driving instructions ("Any deviation from this
specification gets written into docs/adr/ as a numbered decision record, not
left implicit in code"), they're recorded here. None of them simplifies,
omits, combines or weakens a mandatory security, payment, audit, idempotency,
failure-handling or test requirement — each is either a necessary extension
to an under-specified area, or a correction where the doc's own illustrative
text lagged its own fuller specification.

## Decisions

### 1. `PurchaseIntent` and `PolicyContext` schemas are inferred

§8 gives full JSON schemas for Mandate (§8.1), DecisionRecord (§8.2),
AuditEntry (§8.3), and Quote/AgentEnvelope (§8.4) — but not for
`PurchaseIntent` or `PolicyContext`, the two parameters `evaluate()` takes
alongside `Mandate` (§10, entry point signature). Both were built from:

- §10.1's rule table (currency, category, merchant, unit/total caps,
  txn count, nights/rooms, refundable, price delta, catalog version,
  mandate_spec_hash/intent_hash);
- the `rule_trace` example in §8.2, whose field names (`unit`/`limit`,
  `requested`/`reserved`/`cap`, `used`/`limit`, `item_refundable`/`required`,
  `quoted`/`current`/`bps`) fix the exact shape each rule's inputs must take;
- §10.3's own property-test code, which references `intent.total_minor` and
  `mandate.bounds.max_total_minor` directly — confirming the field names
  chosen here match what the architecture's own tests expect.

Money fields on both (`unit_price_minor`, `total_minor`,
`quoted_total_minor`, `current_total_minor`, `reserved_minor`) are
`StrictInt`, same as Mandate's bounds — the "no float in any money-typed
field" requirement (§28 P1 blocker) applies uniformly, not just to Mandate.

### 2. `decision_id` and `decision_ttl_s` are frozen inputs on `ctx`, not generated inside `evaluate()`

§10's entry point docstring is explicit: "Pure. No I/O, no wall clock, no
randomness, no exceptions escape." Minting a `decision_id` inside
`evaluate()` means generating a ULID — random bits, by construction — inside
a function whose own contract forbids randomness. Reading
`DECISION_TTL_S` inside `evaluate()` means domain code reaching into
config, which contradicts §26's DESIGN RULE that only `config.py` reads the
environment, and contradicts §6's "no SDK, no framework" restriction on the
domain layer generally.

Both are therefore caller-supplied fields on `PolicyContext`, exactly the
same pattern §10 already uses for `ctx.now` (injected timestamp) and
`ctx.reserved_minor`/`ctx.txn_count` (injected snapshots). The application
layer (P2+) or, at P1, `cli.py`'s `policy-check` command reads the real
clock and mints the ULID; `evaluate()` only ever turns already-frozen inputs
into a record. This is what keeps `test_deterministic` (§10.3) true in the
strict sense — same `(mandate, intent, ctx)` in, byte-identical
`rule_trace`/`verdict`/`inputs_digest` *and* `decision_id` out — rather than
relying on excluding `decision_id` from the comparison to paper over hidden
internal randomness.

### 3. Mandate lifecycle `status` lives outside the `Mandate` model, in `state_machine.py`

§8.1: "spec_hash covers every field except itself and the signature,"
computed over JCS of everything else. §9.2 I-M1: "A LOCKED mandate is
byte-immutable." If `status` were a field on `Mandate`, it would be swept
into `spec_hash`'s payload — and then advancing a locked mandate from
LOCKED to EXECUTING to SETTLED (§9.1's own transition table) would change
`spec_hash` on every transition, which directly breaks I-M1 and I-M2 (§9.2:
"spec_hash recomputed at any later time MUST equal the stored value").

`MandateStatus` and the explicit transition table with guards (§9.1, done
in full per this phase's requirements) live in
`domain/mandate/state_machine.py`, operating on `(current_status, trigger,
guard_context)` rather than on a status field carried by `Mandate` itself.
One `Mandate` model still covers the whole lifecycle (`spec_hash`/`signature`
are `None` pre-lock, per §9.1's DRAFT/PENDING_CONFIRM states) — only
`status` is kept external to what gets hashed.

### 4. `integrity.binding` (rule 12) checks two things

§10.1 gives this rule one line: "mandate_spec_hash and intent_hash match."
Two independent checks satisfy that line, both grounded elsewhere in the
document:

1. `intent.mandate_spec_hash == mandate.spec_hash` — the buyer-agent's
   claimed mandate version matches the mandate's actual current hash. This
   is the domain-layer half of §11 gate G2 ("sha256(intent) ==
   decision.intent_hash") and directly implements the mandate-tampering
   control in §21's threat table ("spec_hash recomputed and signature
   re-verified on every money action; mismatch halts the transaction").
2. `intent.intent_hash == compute_intent_hash(intent)` — the intent is
   self-consistent, i.e. its claimed hash actually matches its own content,
   using the identical sha256(JCS(...)) pattern §8.1 defines for
   `spec_hash`. This is what makes `intent_hash` meaningful as "binds this
   decision to ONE exact intent" (§8.2) rather than an arbitrary
   caller-supplied string nothing ever verifies.

### 5. The P1 exit-criteria CLI example says "8 rules evaluated"; the real output says "12"

§28's P1 CLAUDE CODE PROMPT is unambiguous: "All twelve policy rules from
§10.1, evaluated in order, ALL rules always run." §10.1's table lists
exactly twelve. The `python -m actl.cli policy-check ...` illustrative
output block a few lines above that instruction — "8 rules evaluated, 1
failed" — predates the twelve-rule table being finalized elsewhere in the
same document; it's the same class of artifact as P0's illustrative
`"migration":"0001"` not matching the real foundation-revision id. The
actual `policy-check` output here correctly reports **12 rules evaluated, 1
failed**, matching the explicit twelve-rule requirement rather than the
stale example count.

### 6. Hypothesis strategies correlate administrative fields via `st.shared`

§10.3 gives `mandates()` and `intents()` as independent `@given` parameters.
Drawn fully independently, category/nights/rooms/currency/mandate_spec_hash
essentially never coincide between an independently-drawn mandate and
intent (confirmed empirically: 0 ALLOWs in 1,000 examples before this
change). Since `test_never_allows_above_total_cap` and
`test_monotonic_in_amount` are implications ("if ALLOW then..."), they
still *pass* vacuously in that state — but they stop being meaningful
tests of the cap logic, which is the opposite of §10.3's own framing
("Hypothesis generated ten thousand mandate/intent pairs and could not
find one where the engine allowed a spend above its cap" — a claim that
requires ALLOW to actually occur).

`tests/property/strategies.py` uses `hypothesis.strategies.shared(...,
key=...)` so category, nights, rooms, currency and the mandate/intent hash
pair always agree between the two independently-declared strategies,
leaving money caps, the temporal window, refund policy, merchant blocking
and price drift as the dimensions that vary — the fields the four
properties are actually about. This raised the ALLOW rate from 0/1000 to
roughly 10/100 per test run, without touching `evaluate()` or any rule.

## Consequences

- P2 (persistence) must set `down_revision` and any lifecycle-status
  storage with decision 3 in mind: `status` is a database column, not a
  Mandate JSON field, and must never be folded into what gets hashed.
- P2/P6 (ledger, gate) must supply `PolicyContext.decision_id` and
  `.decision_ttl_s` when calling `evaluate()` — they are required
  constructor arguments, not optional/defaulted, by design (decision 2).
- The `PurchaseIntent`/`PolicyContext` field names chosen here (decision 1)
  become the de facto contract for whatever later phase produces them (the
  buyer/merchant agent protocol, P7) — if that phase's actual wire format
  differs, reconcile there rather than silently diverging from this ADR.
