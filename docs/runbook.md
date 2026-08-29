# ACTL operator runbook

§28 P9 instruction 6. One section per §20 failure mode (F1–F10): the
detection signal and where to look for it, the immediate containment
action, what must never be retried automatically, the safe recovery path,
how to verify the system actually reached a terminal state with the
reserved balance at zero and the audit chain intact, and when to escalate.

Every command below is a real `actl` subcommand (`uv run python -m actl.cli
<command>`, or `actl <command>` if installed) or a `make` target already
wired into this repository — nothing here is aspirational.

Figure 20.1's invariant underlies every section: **no failure path may
exit without (a) a terminal state, (b) a released reservation, and (c) an
audit entry.** If a live incident doesn't match one of these ten shapes,
that invariant itself has been violated — treat it as an F10-class event
(see below) until proven otherwise.

## Quick reference

| Mode | Detection | Class | Auto-recovers? |
|------|-----------|-------|-----------------|
| F1 | `STALE_PRICE` in an `order.proposed` audit payload | Policy | Yes — one auto re-quote |
| F2 | `PROVIDER_DECLINED`, saga step `C2_VOID`/`C1_RELEASE` | Terminal | Yes — compensation |
| F3 | `payment.result` audit entry with `payload.source="reconciler"` | Transient | Yes — reconciliation poller |
| F4 | Second delivery of the same `provider_event_id` returns `outcome=duplicate` | Transient | Yes — absorbed silently |
| F5 | `TransientProviderError` from the provider adapter | Transient | Yes — bounded retry, same idempotency key |
| F6 | `RankingResult.degraded=true` / `ClarificationNeeded` from U1 | Transient | Yes — deterministic fallback |
| F7 | Same `degraded=true` signal as F6 (see F7 section — no distinct code today) | Policy | Yes — deterministic fallback |
| F8 | `MANDATE_EXPIRED` from Gate G1 on a later money action | Policy | **No — `actl sweep` is a manual step** |
| F9 | `BUDGET_EXCEEDED` from Gate G4 | Policy | Yes — real Postgres row lock |
| F10 | `actl verify-chain` reports `CHAIN BROKEN` | Integrity | **No — halts and waits for a human** |

---

## F1 — Price changes between quote and order

**Detection signal / where to inspect.** Gate G5's `catalog.freshness`
rule denies with reason code `STALE_PRICE`. Look for an `order.proposed`
audit entry whose payload has `"verdict": "DENY"` and
`"reason_codes": ["STALE_PRICE"]`, immediately followed (same `trace_id`)
by a second `order.proposed` entry — this second entry is the automatic
re-quote's own decision. `catalog_items.version` for the SKU will be
ahead of the `catalog_version` the original quote pinned.

**Immediate containment.** None required — detection and recovery both run
through the real production path: `application.agents.merchant.
handle_order_propose`, the exact function real `order.propose` HTTP
traffic is dispatched to (`interfaces/agent/routes.py`). `application.
recovery.propose_with_one_requote_on_stale_price` (the same path `actl
demo --scenario stale_price` exercises) plays the buyer's own client role
for both the stale attempt and the retried attempt, calling that same
function twice — never a parallel or bypassed code path (docs/adr/0010
decision 15).

**Must not retry automatically.** A second `STALE_PRICE` after the one
automatic re-quote must not be retried again — return the deny to the
human with the real, current price. Looping re-quotes on a rapidly moving
price is how a bounded agent becomes unbounded.

**Safe recovery.** Nothing manual for a single occurrence. If STALE_PRICE
is firing for *every* order against one SKU, check whether a catalog-admin
job or `scripts/tamper.py`-style out-of-band mutation is repeatedly
bumping `catalog_meta.version` faster than quotes can be pinned and
consumed.

**Verification.**
```
uv run python -m actl.cli verify-chain --from <first_seq> --to <last_seq>
```
confirms both the DENY and the ALLOW decisions are chained and intact.
Confirm the order's `amount_minor` equals the *re-quoted* total, never the
stale one, and that the reserved balance for the mandate is back to zero
(`account(mandate_id, "reserved")` nets to 0 once the order settles or is
denied).

**Escalation.** Sustained, high-volume STALE_PRICE across many distinct
mandates simultaneously — investigate the catalog-admin pipeline itself,
not individual orders.

---

## F2 — Payment declined by the provider

**Detection signal / where to inspect.** `saga.complete_purchase` returns
`status="COMPENSATED"`, `step="C2_VOID"` (or `C1_RELEASE`). Audit trail has
a `compensation.applied` entry with `payload.reason` in
(`"payment_declined"`, `"order_creation_failed"`). Order row transitions
to `status="FAILED"`; mandate transitions to `MandateStatus.COMPENSATED`.

**Immediate containment.** None — compensation already ran synchronously
inside `complete_purchase` before it returned.

**Must not retry automatically.** Never retry a decline with a new
attempt against the *same* mandate/intent. §20's own design rule: "A
declined payment, a policy denial and a validation failure are terminal
and MUST NOT be retried." A blind retry on a genuine decline just
re-declines and burns the mandate's `max_transactions` budget for nothing.

**Safe recovery.** A human must obtain a new mandate or a different
payment method before any further attempt — nothing in this codebase
auto-issues one. There is no compensating action left to run: C1
(release reservation) and C2 (void order) have already executed.

**Verification.**
```
uv run python -m actl.cli verify-chain --from <seq> --to <seq>
```
over the trace's own seq range; confirm reserved balance for the mandate
is exactly 0 and that replaying `complete_purchase` for the same
`saga_id` is a no-op (same ledger entry count before/after — this is
exactly what `tests/chaos/test_f2.py` asserts).

**Escalation.** A decline *rate* spike (not one order) — check the
provider's own status page first, then look for a fraud/card-testing
pattern (many declines across many different mandates in a short window).

---

## F3 — Webhook never arrives

**Detection signal / where to inspect.** The reconciliation poller
(`_reconcile_loop` in `actl.worker`, polling every 10s, threshold
`settings.reconcile_after_s` = 45s by default) finds an order stuck
non-terminal past that threshold and settles it from a direct provider
poll. Look for `worker.reconciled` in the worker's structured logs, and a
`payment.result` audit entry with `payload.source="reconciler"` (as
opposed to `"webhook"`).

**Immediate containment.** Confirm the `actl.worker` process is actually
running (`ps`/your process supervisor) — if it is down, non-terminal
orders will never be reconciled at all, regardless of how long you wait.

**Must not retry automatically.** Don't manually re-call `create_order`
or re-propose the same intent while an order is merely *pending
reconciliation* — it may already be authorized/captured on the provider
side; a manual retry risks a second, real charge for the same intent.

**Safe recovery.** If the worker was down, restart it — the poller and
its handler (`application.payment_service.reconcile_non_terminal_orders`)
are at-least-once and idempotent, so the very next tick picks up any
orders that were missed. Note the scope boundary: reconciliation updates
only `orders`/`payments` state and writes the audit entry — it does not
touch the ledger directly; ledger movement happens through the normal
gate/saga capture path the reconciled state then unblocks.

**Verification.** Order reaches a terminal `status` (`CAPTURED` or
`FAILED`); audit entry confirms `source="reconciler"`; re-run
`verify-chain` over the affected seq range.

**Escalation.** The same order still hasn't reconciled after several
polling intervals — check the circuit breaker for the payment provider
(`CircuitBreaker(name="razorpay", ...)`) isn't stuck open, and check
provider connectivity directly.

---

## F4 — Duplicate webhook delivery

**Detection signal / where to inspect.** `process_webhook_delivery`
returns `outcome="duplicate"` (or the CLI's `replay-webhook` prints
"duplicate, absorbed (no state change)"). The unique constraint on
`webhook_events.provider_event_id` is what makes this structurally
impossible to double-apply — the claim step itself writes no audit entry
at all (by design: verify-then-dedup is a single indexed INSERT, nothing
else, so an invalid or duplicate delivery never reaches the audit log or
touches order state). The actual order transition, and the one
`payment.result` audit entry (`payload.source="webhook"`) it produces,
happens exactly once, later, when `process_unprocessed_webhooks` (the
worker's webhook loop) applies the *first* accepted claim — a duplicate
delivery never reaches that step at all.

**Immediate containment.** None — this is the expected, designed
behaviour, not an incident.

**Must not retry automatically.** Never manually re-apply a webhook body
"just in case" — the dedup constraint plus the separate claim/apply split
(`process_webhook_delivery` claims, `process_unprocessed_webhooks` applies)
already guarantees at-most-once application.

**Safe recovery.** Nothing to recover — the second delivery was already
a no-op by construction.

**Verification.** Count ledger entries for the order's `saga_id`/`ref_id`
before and after the duplicate delivery — they must be identical (see
`tests/chaos/test_f4.py`).

**Escalation.** An unusually high rate of duplicate deliveries for the
same handful of events — check the provider's webhook retry
configuration or a broken TLS handshake causing it to keep retrying a
delivery it thinks failed.

---

## F5 — Provider timeout on order creation

**Detection signal / where to inspect.** `TransientProviderError` raised
by the provider adapter's `create_order`. `application.payment_service.
create_provider_order` retries this internally, up to `settings.
max_retry_attempts` (3) times with full jitter (`platform.retry.
retry_with_full_jitter`), entirely inside one `saga.begin_purchase` call
— the same `attempt_no` (and therefore the same idempotency key,
`compute_idempotency_key(mandate_id, intent_hash, attempt_no)`) covers
every one of those internal retries. Look at the provider's own
idempotency key store (`idempotency_keys` table) for a single key with
`state="COMPLETED"` despite the provider having logged multiple prior
timeouts.

**Immediate containment.** None — the bounded retry already runs
synchronously inside `saga.begin_purchase`.

**Must not retry automatically.** Never manually re-invoke order creation
for the same intent with a *freshly generated* idempotency key after the
saga's own 3 attempts are exhausted — that defeats the entire point of
the idempotency key and can create a genuine second order.

**Safe recovery.** If all 3 attempts are exhausted, the saga fails and its
reservation is released automatically — same as any other terminal deny.
A human decides whether to re-propose as a brand-new intent (new
`intent_hash`, new saga) once the provider is confirmed healthy again.

**Verification.** Exactly one `order_id` ever exists for that
`(mandate_id, intent_hash)` pair regardless of how many of the 3 attempts
ran; reserved balance is zero if all attempts failed.

**Escalation.** The circuit breaker for the payment provider opens (5
failures in 60s) — that's a real, sustained outage signal; stop
"helping" with manual retries until the breaker naturally closes.

---

## F6 — LLM unavailable or rate-limited

**Detection signal / where to inspect.** `RankingResult.degraded=true`
from `rank_candidates` (U2), or a `ClarificationNeeded` result from
`extract_mandate_draft` (U1) even though the user gave real details. This
is the *expected*, designed behaviour whenever `LLM_ENABLED=false`, the
Groq circuit breaker is open, or Groq itself is down — `NullLLMClient`/
`GroqClient` both surface this identically as `LLMUnavailable`.

**Immediate containment.** None. §17 Figure 17.1's hard boundary holds by
construction: the money transaction (`create_quote` → the real Money
Action Gate → `saga.complete_purchase`) never imports an `LLMClient` at
all, so it is structurally unaffected.

**Must not retry automatically.** Don't retry the LLM call yourself
outside the existing repair-loop/circuit-breaker machinery — U1/U2/U3
already fall back deterministically and never block the transaction.

**Safe recovery.** Nothing required for the transaction. If the outage is
real and ongoing, check Groq's own status and the breaker's `open_until`;
during a confirmed incident it is safe to deliberately set
`LLM_ENABLED=false` cluster-wide to force the deterministic path and stop
wasting calls against a down dependency.

**Verification.** `actl demo --scenario llm_down` reproduces this
end-to-end on demand — the transaction still reaches `CAPTURED` and the
audit trail is identical in shape to a non-degraded run; only the U2
rationale text differs (empty instead of LLM-authored).

**Escalation.** Sustained LLM outage affecting a large share of traffic —
this is a vendor-dependency incident, not an ACTL bug; escalate to
whoever owns the Groq relationship if the outage extends beyond a normal
retry/backoff window.

---

## F7 — LLM names a SKU that does not exist

**Detection signal / where to inspect.** `domain.agent.buyer.
apply_llm_ranking` requires `ranked_skus` to be an exact permutation of
the supplied candidate SKUs; any SKU outside that set is a hard rejection
of the *entire* response (not just the invented entry). Operationally,
this surfaces as the **same** `RankingResult.degraded=true` signal as F6
— there is currently no separate reason code or audit entry that
distinguishes "the LLM hallucinated a SKU" from "the LLM was simply
unavailable" (a documented, deliberate simplification — see
`docs/adr/0010`). If you need to tell the two apart during an incident,
you currently have to correlate with whether the LLM/provider was known
to be up at the time.

**Immediate containment.** None — `rank_candidates` never raises; the
deterministic price-ascending/rating-descending fallback (`domain.agent.
buyer.rank`) is used instead, and the real candidates are untouched (no
price, rating, or availability field can be altered by an LLM response,
by construction).

**Must not retry automatically.** Don't re-send the same prompt hoping
for a valid response on this turn — U2 has already produced a safe,
correct fallback; there's nothing to retry into.

**Safe recovery.** None needed for a single occurrence. If hallucinated
SKUs are frequent, that's a prompt/model-quality issue worth a schema or
prompt review — not an ACTL money-path issue, since money is structurally
unreachable from here (`tests/architecture/test_boundaries.py::
test_conversation_module_cannot_reach_the_gate_or_a_payment_provider`).

**Verification.** `result.items` contains only SKUs that were in the
original candidate list, in deterministic order; `result.rationale` is
empty (the model's rationale text for a rejected response is discarded
wholesale, never partially trusted).

**Escalation.** A sustained high rate of hallucinated SKUs — treat as a
model-quality regression; consider disabling LLM ranking
(`LLM_ENABLED=false`) while the prompt/model is reviewed.

---

## F8 — Mandate expires mid-flight

**Detection signal / where to inspect.** Gate G1 (mandate validity,
re-read from the DB on every money action) denies the *next* money action
against an already-expired mandate with reason code `MANDATE_EXPIRED`.
The reservation from the mandate's earlier, still-valid attempt is left
`HELD` — it does not release itself.

**Immediate containment.** Stop proposing further money actions against
that mandate; G1 will keep denying them deterministically, so no
over-spend risk exists while you investigate. **This is the one failure
mode with no automatic background recovery today** — `application.
ledger_service.sweep` exists and is fully tested (`tests/chaos/test_f8.
py`) but is not wired into any `actl.worker` loop.

**Must not retry automatically.** Don't attempt the same money action
again expecting a different result — the mandate is expired, full stop.
Don't manually release the reservation via a raw SQL `UPDATE` — always go
through `ledger_service.sweep` (via `actl sweep`) so the release is
audited (`reservation.expired`) and idempotent.

**Safe recovery.**
```
uv run python -m actl.cli sweep --ttl-s <reservation_ttl_s>
```
(defaults to `settings.reservation_ttl_s`, 300s) force-releases any HELD
reservation older than the TTL and writes a `reservation.expired` audit
entry per reservation released. A human must then issue a **new**,
freshly signed mandate for the buyer — nothing in this codebase
auto-issues a replacement, by design (§20: "ask the human for a fresh
mandate"). If an F10 integrity halt is *also* active, `actl sweep` refuses
outright (`REFUSED: integrity halt active (<reason>) -- see docs/
runbook.md F10`, exit code 1) rather than releasing a reservation the
untrusted audit trail could not correctly record — resolve F10 first.

**Verification.** `actl sweep` prints the swept `ref_id`(s); confirm the
mandate's reserved balance is back to 0 (query
`ledger_entries` for `account(mandate_id, "reserved")` — there is no
standalone CLI balance-query command today beyond what `actl demo`
prints for its own scenarios). Re-running `actl sweep` immediately after
must sweep nothing (already-released reservations are silently skipped,
never double-released).

**Escalation.** If `HELD` reservations are accumulating faster than your
sweep cadence clears them, that's a real budget-exhaustion risk — either
schedule `actl sweep` more frequently (e.g. via cron) or treat wiring the
sweeper into `actl.worker`'s own background loop as a P10 backlog item.

---

## F9 — Concurrent requests exceed the cap together

**Detection signal / where to inspect.** Gate G4's real Postgres row lock
admits only as many concurrent attempts as the mandate's remaining budget
allows; every loser is denied with reason code `BUDGET_EXCEEDED`. Only
the admitted attempts produce a `budget.reserved` audit entry — losers
produce no reservation and no order at all.

**Immediate containment.** None — the row lock inside one transaction is
what prevents the overspend in the first place; there's nothing to react
to mid-incident.

**Must not retry automatically.** Don't automatically retry a
`BUDGET_EXCEEDED` denial — it means the mandate's budget is genuinely
exhausted at that instant, not a transient failure. §20's own transient/
terminal split classifies this as **Policy**, not **Transient**, exactly
so it is never bounced through the retry path.

**Safe recovery.** None needed. If the loser's purchase should still
happen, that requires a new, larger mandate from the human — not a retry
of the same one.

**Verification.** Reserved balance for the mandate equals exactly the sum
of the admitted attempts' amounts, never more (I-M4, §9.2); count of
created orders equals the admitted count exactly.

**Escalation.** If legitimate traffic is being denied at a rate that
suggests the cap itself is set too low for real usage, that's a
policy/business conversation with whoever owns mandate limits — not an
incident.

---

## F10 — Audit chain integrity broken

**Detection signal / where to inspect.**
```
uv run python -m actl.cli verify-chain --from 1 \
  --to "$(uv run python -m actl.cli chain-head | grep -oE 'seq=[0-9]+' | cut -d= -f2)"
```
(this is exactly what `make verify` runs) reports `CHAIN BROKEN at
seq=<N>` with the expected vs. computed hash and a reason (`payload hash
does not match the recorded entry_hash`, or a sequence gap). This is also
checked automatically wherever `verify_chain_and_halt_on_failure` runs (a
periodic verifier job, or at process startup, per your deployment) — a
failure there durably trips the halt: a single row in the `integrity_halt`
table (`SELECT * FROM integrity_halt WHERE id='default'` shows `halted`,
`reason`, `tripped_at`, `tripped_seq`), not process memory. Every process
reading the same database — every API instance, the worker, `actl demo`,
`actl sweep` — sees the same row and refuses money-affecting work
immediately, including instances that were already running before the
trip and instances started fresh afterward (docs/adr/0010 decisions 2, 16).

**Immediate containment — do this first, before anything else.** The
moment a break is confirmed:
1. Every money-affecting entry point already refuses on its own —
   `execute_money_action` (API, `actl demo`), `application.ledger_service.
   sweep` (`actl sweep`), and `actl.worker`'s two loops
   (`process_unprocessed_webhooks`, `reconcile_non_terminal_orders`) all
   check the same durable row and deny/raise immediately. You do not need
   to race to stop traffic at the load balancer the way a process-local
   halt would have required — confirm the row is set
   (`SELECT halted FROM integrity_halt WHERE id='default'`) and every
   instance sharing that database is already covered. Still pull traffic
   out of rotation if you want a hard stop for other reasons (e.g. to
   freeze the system for forensics), but it is not required for the halt
   itself to hold.
2. Do not attempt to "fix" the row with another `UPDATE`. Every touch to
   `audit_log` after the trigger is disabled is itself forensic evidence;
   preserve the database state as-is (snapshot/backup it) before any
   further action.

**Must not retry automatically — ever.** No money action, no matter how
routine, should be allowed to proceed until the chain is independently
re-verified as valid. This is the one failure class with **no**
compensating action baked into the codebase: "an integrity failure stops
the system rather than degrading it" (§20's own judge signal).

**Safe recovery.** This requires a human-led investigation, not a
runbook checklist that can be executed mechanically:
1. Identify the exact `seq` and row from the break report.
2. Compare against an independent source of truth — a read replica taken
   before the tamper, an offline export bundle made earlier
   (`uv run python scripts/export_audit_bundle.py --to <seq>`,
   verified with the bundle's own standalone `verify_bundle.py`), or a
   Merkle checkpoint anchor if one was configured.
3. Determine the full extent of the tampering (a single row, or more) —
   re-run `verify-chain` across the full range once you believe you know
   the boundary; a single break can mask a wider one that was papered
   over.
4. Only once the row(s) are confirmed and remediated (restored from a
   trusted source, or the corrupted range is formally excised with a
   documented incident record) is it safe to clear the halt. There is no
   `actl` command and no application code path for this anywhere in
   `src/actl/` — §20 names no recovery action for F10 alone among all ten
   failure modes, so clearing one is a direct, manual database statement
   an operator runs themselves, after the investigation above, never a
   convenience method that could fire by accident or automation:
   ```sql
   UPDATE integrity_halt
   SET halted = false, reason = NULL, tripped_at = NULL, tripped_seq = NULL,
       cleared_at = now(), cleared_by = '<your name/ticket id>'
   WHERE id = 'default';
   ```

**Verification.** `make verify` (same command as above) must report
`CHAIN VALID` across the **entire** chain (not just the
segment that broke) before resuming traffic. Confirm every mandate's
reserved balance still reconciles against what the (now-trusted) audit
trail implies, since money actions may have been silently denied for the
duration of the halt.

**Escalation.** Always, immediately, no exceptions — F10 is a security
incident by definition, not an operational hiccup. Page whoever owns
security/on-call for this system before attempting any recovery step
above; do not attempt silent, solo recovery on a tampered financial audit
log.

---

## Prerequisites and outputs

- `make up` — starts the dev Postgres/Redis (docker compose); required
  before `scripts/demo.sh`/`make record`/any hand-run `actl` command
  above (`verify-chain`, `sweep`, `demo`, ...). Not required for `make
  chaos` or `make demo`, which both run against their own disposable,
  isolated testcontainers Postgres.
- `make migrate` — applies migrations to whichever `DATABASE_URL` is
  configured (the `make up` database).
- `make chaos` — runs all ten F1–F10 chaos tests (`tests/chaos/`),
  offline and deterministic (`LLM_ENABLED=false PAYMENT_PROVIDER=
  simulator` forced), against isolated testcontainers.
- `make demo` (`scripts/run_demo_suite.py`) — prints an explicit six-row
  PASS/FAIL summary for all six `application.demo.DEMO_ITEMS`: the five
  §20.1 scenarios plus `verify_chain` (the sixth, closing `actl
  verify-chain` command), each compared byte-for-byte against its own
  committed golden fixture (`fixtures/golden_traces/demo_*.json`, six
  files) and independently re-verified offline — full parity across all
  six, no row with weaker evidence than the others. `verify_chain`'s row
  additionally exports the whole assembled chain as a standalone audit
  bundle and verifies it with its own generated, dependency-free
  `verify_bundle.py`, run as a separate process, on top of its golden-
  fixture comparison. Runs against its own fresh, isolated testcontainers
  Postgres every time — always safely re-runnable, no `make up` needed.
  `pytest tests/golden` runs the same six-item comparison under pytest,
  for CI reporting/coverage.
- `./scripts/demo.sh` — the live, human-facing counterpart: runs the same
  five scenarios through the real `actl demo` CLI against whichever
  database `make up`/`make migrate` configured, then `actl verify-chain`
  as the closing sixth command. Requires a freshly migrated database (see
  the script's own header) — re-running it against a database that
  already has a prior run's rows will collide on the deterministic ids.
  `make record` wraps this with `script`(1) to capture a full transcript.
- `make verify` — validates the live audit chain head-to-tail
  (`actl verify-chain`, needs `make up`'s database populated by something
  first), confirms all six committed golden fixture files are present
  (fails by name, `test_all_six_golden_fixture_files_are_present`, if any
  is missing), and independently re-verifies all six fixtures' own hash
  chains offline (no database needed for that half).
- `LLM_ENABLED=false PAYMENT_PROVIDER=simulator uv run python scripts/
  generate_demo_golden_traces.py` — regenerates the golden fixtures after
  a deliberate, intentional behaviour change (never run this to make a
  failing test pass without understanding why the trace changed first).
  Both env vars matter: `payment.intent`'s audit payload echoes
  `settings.payment_provider` verbatim regardless of which adapter object
  is actually used, so an unset `PAYMENT_PROVIDER` bakes in whatever your
  shell's default is instead of `"simulator"`.
