# 0009 — P8 LLM layer, guardrails, and growth instrumentation decisions

Status: Accepted
Date: 2026-08-29

## Context

P8 ("LLM layer — Groq, guardrails, fallback", §28) adds the three bounded
LLM capabilities (§17: U1 mandate extraction, U2 candidate ranking, U3
audit narration) strictly on top of a system that already works
completely without them, plus §22.2's growth instrumentation (four
outbox events, `GET /metrics/growth`, `actl growth --seed --sessions`).
`LLM_ENABLED=false` for every regression command; DEMO_REPLAY cassettes
and test doubles cover every LLM-touching code path, so normal CI makes
no Groq network call at all. Several places required an interpretive,
corrective, or explicitly-authorized deviation from the architecture's
literal text; they are recorded here per this project's standing practice
(see ADR 0006-0008 for the P5-P7 precedent).

## Decisions

### 1. GROQ_MODEL is `openai/gpt-oss-120b`, not the architecture's literal `llama-3.3-70b-versatile` — explicitly authorized

Before writing any code, `console.groq.com/docs/deprecations` was checked
per instruction: it states verbatim "on June 17, 2026, we emailed users
to announce the deprecation of `llama-3.1-8b-instant` and
`llama-3.3-70b-versatile`," shutdown date `08/16/26`, "We recommend
migrating to `openai/gpt-oss-120b` or `qwen/qwen3.6-27b`," and "This
deprecation applies to free and developer-tier usage; enterprise
customers with a committed-spend contract are not affected." This build
is explicitly free-tier/test-mode-only (`config.py`'s own docstring), and
today is 13 days past that shutdown date — the architecture's literal
model choice would fail at the API boundary on every live call. Reported
precisely and asked before continuing, per instruction; the user chose
`openai/gpt-oss-120b`, Groq's primary recommended replacement, confirmed
via GroqDocs to support JSON mode. `config.py`'s `groq_model` default is
the only place this is pinned — a future model change is a one-line edit.

A follow-up correction updated `docs/architecture.md` itself: its
`.env.example` block (§26) and the P8 CLAUDE CODE PROMPT text (§28) both
originally showed the literal, now-retired `llama-3.3-70b-versatile`.
Both now show `GROQ_MODEL=openai/gpt-oss-120b` with an inline note dating
the provider-forced migration and pointing back to this decision — the
architecture document's own configuration reference stays consistent
with what this build actually ships, rather than leaving a stale example
value that would mislead a future implementer working from the spec
alone. `GROQ_MODEL` remains the single configuration knob; nothing in
`src/` hardcodes either model string outside `config.py`'s own default
(checked directly: `grep -rn "llama-3.3-70b-versatile\|gpt-oss-120b" src/`
matches only `config.py`).

### 2. JSON *object* mode, not strict `json_schema` structured outputs

Groq's structured-outputs docs show a newer `response_format: {"type":
"json_schema", "json_schema": {...}, "strict": true}` shape as well as
the older `{"type": "json_object"}` "JSON mode." §17.2 says "JSON mode,"
not "constrained decoding against a schema" — `GroqClient` uses plain
`json_object` mode and relies entirely on the existing, explicitly-
required Pydantic validation (`infrastructure/llm/repair.py`) for schema
enforcement, matching the architecture's own two-layer design ("Output
contract: ... Pydantic / JSON Schema, 2 repair attempts" — the repair
loop, not the API call, is where schema conformance is actually
enforced). This also keeps `GroqClient` correct regardless of whether
`openai/gpt-oss-120b`'s strict `json_schema` support is complete, which
is not independently confirmed.

### 3. `MandateDraft` is its own type, never a `Mandate` with `status=DRAFT`

`domain/mandate/draft.py`'s `MandateDraft`/`ClarificationNeeded` have no
`spec_hash`, no `signature`, and no row in the `mandates` table —
`application/gate.py` never imports `domain.mandate.draft` at all
(checked structurally, not just by convention — see decision 11).
`REQUIRED_SLOTS` includes `category` even though this catalog is
travel.hotel-only today: §9.1's DRAFT guard is "no field inferred from
silence," with no carve-out for "only one possible value," so defaulting
category would be exactly the silent-widening failure mode §9.2's RISK/
GUARD box warns against. Every monetary bound's minor-unit integer is
*computed in code* (`verify_money_evidence`, `x100` arithmetic) from a
verbatim numeral substring the model only points at — never accepted as
a number the model itself calculated, satisfying §17.1's "the model may
not compute or infer an amount" literally rather than by policy.

### 4. U2's referential check is a single exact-permutation test, not a subset check

`domain/agent/buyer.py::apply_llm_ranking` requires `ranked_skus` to be
exactly a permutation of the supplied candidates' own skus — same count,
no duplicates, every one drawn from the allowed set. A response naming
*fewer* than all candidates is rejected exactly like one naming an unknown
SKU (§28 P8 instruction 3's "reject the entire response" is not qualified
to "only for extra SKUs"); a partial ranking that silently dropped a
policy-valid candidate would itself be a subtle way to make an item
disappear without ever formally denying it.

### 5. U3 "a window of audit entries" is interpreted as one entry narrated at a time

§17.1's row says the input is "a window of audit entries," but the
persisted output (`audit_log.narration`) is a *per-row* column with no
row spanning multiple entries. `narrate_entry`/`narrate_and_store` narrate
and store one entry at a time; a caller wanting a "window" iterates a seq
range and calls this once per entry. No new migration was needed:
`migrations/versions/0002_audit_outbox.py` (P2) already created the exact
narration carve-out this phase needed —
`(to_jsonb(OLD) - 'narration') IS DISTINCT FROM (to_jsonb(NEW) -
'narration')` — stronger than the architecture's own excerpted trigger
text (which compares `OLD.narration IS NOT DISTINCT FROM NEW.narration`
alone): P2's version blocks a smuggled change to *any other* column even
if narration also changes in the same statement.
`AuditLogRepository.update_narration` is a bare SQLAlchemy Core
`update()` naming only `narration`, so it can structurally never touch
`entry_hash`/`prev_hash`/`payload` — proven against a real Postgres
container in `tests/integration/llm/test_narration.py`, including that a
direct attempt to change any other column is still rejected by the same
trigger.

### 6. Fencing is a delimiter, not a sanitizer — the security boundary is the validation layer

`infrastructure/llm/prompts/fencing.py::fence` wraps external text in
delimiters plus the exact required preamble and does nothing else to the
content — no stripping, no escaping beyond what `json.dumps` does for
structured payloads. The actual guarantee that an injected instruction
cannot alter schema, authority, prices, bounds, the candidate set, or the
execution path comes from the same code-side checks §17.1 already
requires for other reasons: evidence verification (U1), exact-permutation
SKU validation (U2), and the complete absence of any import path from
`application.conversation`/`application.growth` to
`application.gate`/a payment provider (checked structurally in
`tests/architecture/test_boundaries.py`, decision 11). `tests/integration/
llm/test_prompt_injection.py`'s adversarial tests are deliberately written
against `ScriptedLLMClient` responses that *comply* with an injected
instruction, proving the validation layer holds even when fencing is
assumed to have already failed — fencing raises the bar a real model has
to clear; it is not itself the thing being tested.

### 7. The Redis rate limiter is a real, atomic, Lua-scripted token bucket — corrected from an earlier fixed-window approximation

The first version of `infrastructure/cache/rate_limit.py::TokenBucketLimiter`
was `INCR` + `EXPIRE` against a clock-derived per-minute bucket key —
simpler, but able to admit up to ~2x `limit_per_min` across a single
window boundary, and not a token bucket in any real sense (no continuous
refill, no burst/sustained-rate distinction). Corrected to a genuine
lazy-refill token bucket, implemented as a single Lua script
(`_TOKEN_BUCKET_LUA`) executed via `EVAL`/`EVALSHA`: read the bucket's
`(tokens, ts)` hash, refill proportionally to elapsed time, check and
decrement, write back — all inside one Redis command. Redis executes Lua
scripts to completion on its single command-execution thread, so this
whole read-refill-check-write sequence is atomic with respect to every
other client, including truly concurrent callers on independent
connections — proven directly in `tests/integration/llm/test_rate_limit.py::
test_concurrent_claims_admit_no_more_than_capacity` (50 concurrent
`asyncio.gather`'d callers against capacity 10 admit exactly 10) and
`test_concurrent_claims_across_independent_redis_connections` (same proof
with 30 genuinely separate `Redis` client connections, ruling out "it's
just one Python client's connection pool serializing them").

Capacity and refill both still derive from the single
`LLM_RATE_LIMIT_PER_MIN` config knob, preserving its existing meaning
rather than adding a second value: `capacity = limit_per_min` (max burst),
`refill_per_second = limit_per_min / 60` (so the bucket fully replenishes
over one minute, keeping "requests per minute" as the sustained rate).
This build is one free-tier demo process with `LLM_RATE_LIMIT_PER_MIN=20`
against Groq's own 30 RPM free-tier ceiling for `openai/gpt-oss-120b`
(confirmed on GroqDocs) — comfortable headroom either way, but the
implementation is now correct on its own terms, not merely "good enough
at this traffic volume."

One implementation subtlety worth recording: every Lua script argument
crosses the Redis text protocol as a string, and Lua's default
`tostring()` on a float truncates precision (a stored timestamp like
`1788006200.824076` round-tripped as `1788006200.8241` in testing) —
enough accumulated error, after several calls, to make an exact-refill-
boundary check flake. Fixed by formatting stored floats with
`string.format('%.17g', ...)` (full double precision) instead of
`tostring()`, plus a small epsilon (`1e-7`) on the admit comparison as a
second line of defence.

Unavailability still fails closed, unchanged from the original design
(decision 8 below): `RateLimitUnavailable` on any `RedisError`, converted
by `GroqClient` into `LLMUnavailable`, which every U1/U2/U3 caller already
treats as "fall back to the deterministic path" — proven end-to-end in
`test_redis_outage_falls_back_without_ever_calling_groq`, which spies on
the underlying Groq SDK call and asserts it is never invoked when the
limiter is unreachable. A rate limiter that failed *open* here would turn
a Redis outage into an unbounded retry storm against Groq; failing closed
means the opposite, and money flow is unaffected either way since the
deterministic fallback never depends on the LLM at all.

### 8. The semantic cache fails open; the rate limiter and nonce cache fail closed

`SemanticCache.get`/`.set` swallow `RedisError` and return `None`/do
nothing — a cache is never load-bearing for correctness, so a Redis
outage should cost one extra LLM call (or fall through to the existing
deterministic path if the LLM is *also* down), never raise on its own.
`TokenBucketLimiter.try_acquire` and the P7 `NonceCache.claim` both raise
on the same error, because their job *is* the correctness guarantee
(cost control; replay protection) — this asymmetry is deliberate, not an
inconsistency.

### 9. `BudgetedLLMClient` — a shared per-transaction call counter, needed because U1+U2's repair loops alone can exceed 3

§17.3's "hard ceiling of 3 LLM calls per transaction, asserted in tests"
is stated as a property, not wired to any specific mechanism. Without a
shared counter, a single transaction that hits U1's 2-attempt repair loop
*and* U2's 2-attempt repair loop *and* U3 narration could reach 5 calls.
`application/conversation/budget.py::BudgetedLLMClient` wraps any
`LLMClient`; once `max_calls` (from `settings.llm_max_calls_per_txn`) is
spent, every further call raises `LLMUnavailable` immediately, without
reaching the network — so a caller past the ceiling takes the identical
safe-fallback path a real outage would.
`tests/integration/llm/test_llm_budget.py::test_llm_call_budget_never_
exceeds_3` proves the worst case (both repair loops maxed out) still caps
at 3 real calls and still produces safe fallbacks for all three uses, not
a crash.

### 10. DEMO_REPLAY cassettes are generated by a script against the real prompt builders, not hand-written

`scripts/record_llm_cassettes.py` computes each cassette's filename via
the *real* `canonical_prompt_key` and the *real* `extraction.build_user_
prompt`/`ranking.build_user_prompt`/`narration.build_user_prompt`
functions against fixed demo scenarios, so a genuine `DEMO_REPLAY=true`
run through `extract_mandate_draft`/`rank_candidates`/`narrate_entry`
finds and serves exactly these recordings — proven in `tests/integration/
llm/test_demo_replay.py`, which calls the real `build_llm_client` factory
rather than a test double. Two bugs surfaced and were fixed while first
recording these: (a) two scenarios sharing an identical prompt produced
the same `canonical_prompt_key` and silently overwrote each other on
disk — the script now raises loudly on any key collision instead; (b)
`rank_candidates` filters candidates against the mandate's
`max_unit_minor` *before* the LLM ever sees them, so a cassette recorded
against a candidate list containing an over-budget item never matches
what the real filtered prompt actually contains — every recorded
candidate is priced at or under the demo mandate's cap.

### 11. `application.conversation` and `application.growth` have no import path to the gate or a payment provider — checked structurally

Two new `tests/architecture/test_boundaries.py` fitness functions (§23.4
style, mirroring the existing Razorpay-adapter check): `test_conversation_
module_cannot_reach_the_gate_or_a_payment_provider` and `test_growth_
module_never_imports_groq_or_razorpay`, both static-AST-based like the
pre-existing payment-provider check. This is §17 Figure 17.1's HARD
BOUNDARY ("The LLM has no credential, no write path, and no vote in any
authorization decision") made an executable, whole-import-graph fact
rather than a design intent: LLM output can only ever be *consumed* by
code that separately, unconditionally, goes through the gate the normal
way. A third fitness function, `test_llm_module_has_no_payment_provider_
access_or_credentials`, adapts §23.4's own pseudocode example
(`test_llm_module_has_no_credentials`) to this build's real module path
(`actl.infrastructure.llm`, not a top-level `actl.llm`).

### 12. `application.growth.simulation` is typed against `SimulatorAdapter` directly, not the generic `PaymentProvider` port

Every other application-layer module in this codebase depends only on
the `PaymentProvider` protocol, receiving a concrete adapter injected by
the caller (ADR 0006 decision on dependency inversion). The growth
simulator is the one deliberate exception: its whole purpose is running
under `--seed`/`--sessions` with a guarantee that it "must not contact
Razorpay" (§28 P8 instruction 9) — pinning the parameter type to
`SimulatorAdapter` turns that guarantee into something mypy enforces at
every call site, not just a runtime configuration choice a caller could
get wrong. `test_growth_module_never_imports_groq_or_razorpay` (decision
11) is the accompanying whole-graph proof that the module cannot reach
the concrete Razorpay adapter even indirectly.

### 13. Growth-simulation row identifiers are fresh ULIDs; only the *decisions* are seed-derived

"Reproducible" (§28 P8 instruction 9 / §22.2) is a property of the
printed arm statistics, not of the underlying database row ids: every
stochastic decision (does this session convert, does the customer accept
the upsell) is drawn from `random.Random(f"{seed}:...")`, which Python
guarantees is deterministic for a str seed across processes and machines
regardless of `PYTHONHASHSEED` — but `mandate_id`/`order_id`/`session_id`
still use the platform's normal `new_id()` ULIDs. Deriving row ids from
the seed as well would make re-running the same `--seed` against a
database that already has that seed's data fail on a primary-key
collision; keeping ids fresh while decisions stay seed-derived means the
same `--seed`/`--sessions` always produces the same conversion/attach/
uplift numbers, safely re-runnable against a live, already-populated
database. `tests/integration/growth/test_simulation.py::test_same_seed_
produces_the_same_stochastic_pattern` proves the decision sequence
matches session-for-session across two independent calls with the same
seed.

### 14. A real bug the growth simulator's own seeding surfaced: catalog item `version` must track the live global counter, not a hardcoded `1`

`create_quote` stamps a `Quote.catalog_version` from the catalog item's
*own* `version` column; `handle_order_propose` later compares that
against `uow.catalog.current_version()` (`catalog_meta`'s single global
counter) to detect catalog drift (§10.1 rule 11). `CatalogRepository.
upsert_item` — by design, per its own docstring, "scripts/seed.py only —
idempotent seeding" — never touches that global counter; only
`mutate_price()` bumps it, stamping the mutated item with the new value
in the same transaction. `application/growth/simulation.py::_seed_catalog`
originally seeded its two demo items via `upsert_item` with a hardcoded
`version=1`. In a database where *any* earlier, unrelated price mutation
had already advanced the global counter (which the full test suite's own
`tests/integration/catalog` admin-price tests do, and which sorts before
`tests/integration/growth` in pytest's default alphabetical collection
order), every growth-simulation purchase would fail catalog-drift
verification and the base order would never be created — not a rare
edge case, but a reliable failure under the suite's actual, real
collection order, caught by `tests/integration/growth/test_simulation.py`
during this phase's own full-suite regression run. Fixed by reading
`current_version()` inside the same seeding transaction and stamping both
items with whatever the counter actually is right now, rather than
assuming a fresh-database starting value.

### 15. A second real bug the simulator surfaced: one mandate can fund exactly one settled purchase, ever — the upsell needs its own mandate

The first growth simulator draft reused the *same* mandate for a
session's base purchase and its upsell purchase (`max_transactions=2`,
reasoning that the cap.count rule would admit a second attempt). Debugging
via `outcome.body` showed every upsell attempt rejected `MANDATE_INVALID`,
not `BUDGET_EXCEEDED` as intended. Root cause: `saga.complete_purchase`'s
S5 step (`application/orchestrator/saga.py`) transitions the mandate
`EXECUTING -> SETTLED` the moment *any* order against it settles (§9.1:
"EXECUTING -> SETTLED: Terminal payment success"), and G1 then refuses
every later `order.propose` against a terminal-status mandate regardless
of `max_transactions` (ADR 0007 decision 3: `max_transactions`/cap.count
bounds retries *before* the terminal transition, not a count of separate
completed purchases). One mandate can therefore ever fund exactly one
settled purchase. A second bug in the same fix attempt then surfaced
`QUANTITY_MISMATCH` instead: §10.1 rule 8 checks the intent's nights/
rooms against the *mandate's own* declared `intent.nights`, which had been
hardcoded to the base purchase's value (3) and reused unchanged for the
upsell purchase's different value.

Fixed by minting a second, independent mandate for the upsell attempt —
its own `intent.nights` matching the upsell item, its own budget drawn
per-session from `UPSELL_MANDATE_BUDGET_RANGE_MINOR` (representing the
human's own varying approved add-on ceiling) — rather than reusing the
base mandate at all. This is not a workaround for the two bugs above; it
is the architecturally correct shape once EXECUTING->SETTLED terminality
is taken seriously: the mandate model's own philosophy is that *each*
bounded purchase decision the human confirms is its own authorization, so
an accepted upsell — a fresh decision, distinct from the base purchase —
legitimately gets its own mandate. `_build_mandate` documents both
findings inline. A third, smaller adjustment followed once purchases were
actually succeeding: the upsell item's price was raised above the base
purchase's (270000/night x 3 nights = 810000, vs. the base's 750000) —
the first version priced it lower, which meant a *successful* upsell
pulled AOV *down*, the opposite of what "upsell" should demonstrate.
`tests/integration/growth/test_simulation.py::test_a_converted_session_
always_has_a_base_order` and a real `actl growth --seed demo --sessions 40`
run against a freshly-migrated database both confirm the fixed behaviour:
genuine, seed-varying G4 admits/denies for the upsell, and a positive
revenue uplift once some succeed.

### 16. `GET /metrics/growth` has no `window=` parameter; every session/order the outbox has ever recorded is included

§22.2's endpoint signature is written as `GET /metrics/growth?window=`,
but §18.1's own store-responsibilities table lists no time-partitioned
session table to filter by — the outbox is the fact table, and outbox
rows have no natural "window" boundary beyond `created_at`, which no
instruction ties to a specific windowing semantic (calendar day? rolling
N hours? since-last-checkpoint?). Rather than invent an unspecified
windowing scheme, `compute_growth_metrics` aggregates the complete
history, matching how a real production dashboard would default to
"all time" absent an explicit choice. A future phase that wants a
`window=` filter has an unambiguous extension point:
`OutboxRepository.count_by_event_type_and_arm`/`sum_order_total_minor`
would each need an additional `since:` bound.

## Consequences

- `application.growth`'s direct dependency on `SimulatorAdapter` (decision
  12) means a hypothetical future "run the growth simulation against a
  real provider" mode would need a deliberate, separate decision to widen
  that type — not an accidental side effect of refactoring.
- The fixed-window rate limiter (decision 7) and the fail-open semantic
  cache (decision 8) are both narrow, documented simplifications; neither
  is on any path that a security or money-movement guarantee depends on.
- Every LLM-touching module (`infrastructure.llm.*`, `application.
  conversation.*`, `application.growth.*`) is covered by an architecture
  fitness test proving it cannot reach a payment provider or the gate —
  a future LLM-adjacent module (e.g. a P9 conversation-graph orchestrator)
  should extend `tests/architecture/test_boundaries.py`'s pattern rather
  than introduce a new, unchecked one.
