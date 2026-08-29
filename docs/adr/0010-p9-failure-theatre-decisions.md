# 0010 — P9 failure theatre, demo scenarios, and golden trace decisions

Status: Accepted
Date: 2026-08-29

## Context

P9 ("Failure theatre — injection harness and scenarios", §28) makes every
failure mode in §20 reproducible on demand: a fault-injection harness and
one dedicated chaos test per failure mode (`tests/chaos/test_f1.py` ..
`test_f10.py`), the six §20.1 demo scenarios wired into `actl demo
--scenario`, committed golden traces with byte-for-byte comparison tests,
and `docs/runbook.md` for operators. Fault injection is test/dev-only,
disabled by default, and impossible to trigger from normal application
configuration; normal tests and CI stay fully offline
(`PAYMENT_PROVIDER=simulator`, `LLM_ENABLED=false`). Several places
required an interpretive, corrective, or newly-added-but-minimal
decision; they are recorded here per this project's standing practice
(see ADR 0001–0009 for the P0–P8 precedent). Decisions 15–19 are a
production-readiness correction pass, requested after the first delivery
and before commit: decision 15 replaces decision 1's recovery-only bypass
with a one-line root-cause fix in `handle_order_propose` itself; decision
16 replaces decision 2's accepted process-local limitation with a durable,
cross-process halt; decisions 17–18 close two gaps the correction pass's
own testing surfaced while landing 16; decision 19 makes `make demo`'s
coverage explicit; decision 20 is a second-round correction to 19 itself,
closing a five-vs-six coverage asymmetry a follow-up review caught —
matching ADR 0008 decisions 6/8's own "follow-up correction pass"
precedent.

## Decisions

### 1. F1's recovery path drives the policy engine and gate directly, never through `handle_order_propose`

§20's F1 response is "auto re-quote once, re-evaluate policy, proceed or
deny with real numbers" on Gate G5's `STALE_PRICE`. `application.agents.
merchant.handle_order_propose` (§28 P7) additionally re-derives its own
`intent_hash` from the *live* catalog version to verify a buyer-claimed
hash, before the policy engine ever runs — that P7 security boundary
exists to catch a buyer lying about mandate/intent terms across an
untrusted agent-to-agent hop. Going through it here would make
`STALE_PRICE` structurally unreachable: any real catalog-version drift
surfaces as `INTENT_MISMATCH` first, before the policy engine's own
freshness rule ever runs, so the auto-requote logic would never see the
failure mode it exists to handle (confirmed by running it and getting
`INTENT_MISMATCH` instead of `STALE_PRICE` before this fix).
`application/recovery.py` (`propose_with_one_requote_on_stale_price`) is
therefore a new, additive, parallel call path that drives `domain.
policy.engine.evaluate` and `saga.begin_purchase` directly — the same
layer `tests/integration/gate/test_gates.py` already exercises G5
against. `handle_order_propose` itself is completely unmodified; this is
not a weakening of anything P7 built, only a second entry point at a
different layer for a case where the P7 boundary does not apply (no
buyer/merchant trust hop exists inside one recovery orchestration).

### 2. `IntegrityHalt` is a process-local, in-memory singleton — not a distributed halt

F10 requires that "an integrity failure stops the system rather than
degrading it." `application/integrity.py`'s `IntegrityHalt` is a
module-global dataclass, checked as the first line of `execute_money_
action` before even malformed-request validation, tripped by `audit_
service.verify_chain_and_halt_on_failure`. This genuinely halts every
money action *inside the process that tripped it*, proven by `tests/
chaos/test_f10.py`. It does **not** halt other processes/instances
sharing the same database — a real multi-instance deployment needs a
persisted or broadcast signal (e.g. a `system_status` row every request
path checks, or a pub/sub flag), which is out of scope for this build.
`docs/runbook.md`'s F10 section documents the operational consequence
explicitly: on a confirmed chain break, an operator must pull every app
instance out of rotation manually rather than trust the halt alone to
protect a multi-instance deployment.

### 3. Deterministic ID seeding is a global, opt-in toggle — not a parameter threaded through every call site

§28 P9 instruction 3 requires "seeded IDs" for chaos-test and golden-trace
determinism. `platform/ids.py` gained `seed_deterministic_ids(seed)` /
`reset_ids()`, toggling a module-level `_deterministic_seed`/`_counter`
pair that `ulid()` checks first (deriving from `sha256(f"{seed}:
{counter}")` when seeded, real `time.time_ns()+os.urandom()` otherwise).
Every existing call site (`new_id()`, used throughout `gate.py`,
`catalog_service.py`, `saga.py`, and dozens more) needed zero changes —
minimal blast radius, matching this project's own precedent of preferring
a toggle over invasive parameter threading when the alternative would
touch every caller of a function this central. `application/demo.py`'s
`run_scenario` seeds with `f"actl-demo:{scenario}"` before dispatching
and always resets in a `finally`, so an exception mid-scenario can never
leak deterministic-seed state into whatever runs next in the same
process.

### 4. The catalog-version-sync bug (ADR 0009 decision 14) recurred in P9 and was fixed the same way

`CatalogRepository.upsert_item` (scripts/seed.py-only idempotent seeding)
never bumps `catalog_meta`'s global version counter; only `mutate_price()`
does. Any test or scenario seeding a catalog item with a hardcoded
`version=1` breaks the moment any earlier activity in the same database
has already advanced the counter — the exact root cause ADR 0009 decision
14 first diagnosed in the growth simulator. It reappeared twice this
phase: `tests/chaos/test_f1.py` (deliberately mutates price mid-test, so
its own seed must start in sync with whatever the counter already is) and
`tests/chaos/test_f6.py` (surfaced as a spurious `INTENT_MISMATCH` the
first time the full `tests/chaos` suite ran together, after `test_f1.py`'s
own `mutate_price()` call had already advanced the shared session-scoped
container's counter). Both, and `application/demo.py`'s own `_seed_item`
helper, now read `current_version()` inside the seeding transaction and
stamp the item with whatever the counter actually is, never a hardcoded
`1`.

### 5. A second recurring cross-test issue: the shared chaos-suite Postgres container gets permanently tampered with

`tests/chaos/test_f10.py` deliberately, permanently tampers with one row
(disable the append-only trigger, `UPDATE`, re-enable it — the same
mechanism `tests/integration/audit/test_tamper_detection.py` established
in P3, which also never reverts its own tamper). Any other chaos test
verifying the *whole* shared chain from seq=1 (as `test_f6.py` and
`test_f9.py` originally did) breaks once `test_f10.py` has run in the
same pytest session, regardless of collection order relative to it,
because the corrupted row stays corrupted for the rest of the session.
Fixed with the same `start_seq+1..tail` pattern `tests/integration/gate/
test_gate_concurrency.py` already established for this exact class of
problem: capture the chain's tail *before* the test's own activity, and
verify only that test's own segment, never the whole shared chain.

### 6. "The six §20.1 demo scenarios" = five named `--scenario` values plus `actl verify-chain` as the closing, sixth command

§20.1 lists exactly five `actl demo --scenario <name>` invocations
(`happy_path`, `over_cap`, `stale_price`, `declined`, `llm_down`)
followed by a sixth, differently-shaped command,
`actl verify-chain --from 1 --to 80`, closing the four-minute script.
`SCENARIOS` in `application/demo.py` is therefore exactly those five
names — `--scenario` never accepts a sixth literal value — and
`verify-chain` (already an existing subcommand, wired since P3) is the
six-command backbone's closing step, invoked directly by both `scripts/
demo.sh` and `make verify`, not reimplemented as a demo "scenario."

### 7. F7's "audit the rejection" is the in-memory `degraded=true` signal, not a new persisted audit entry — a real, documented observability gap

§20's F7 response is "reject the response, fall back, audit the
rejection." `domain.agent.buyer.apply_llm_ranking`'s exact-permutation
check does the rejecting; `application.conversation.ranking.
rank_candidates` catches it in the *same* `except LLMUnavailable` block
that also catches a genuine LLM outage (F6), returning the identical
`RankingResult(degraded=True, ...)` either way. No new reason code and no
new audit_log entry were added to distinguish "the LLM hallucinated a
SKU" from "the LLM was simply unavailable" — U2 runs before any quote,
gate, or reservation exists (no `UnitOfWork` is even opened), so there is
no money-path audit trail this rejection could attach to without adding
a new, U2-scoped audit action purely for observability. Rather than
invent one speculatively, this is recorded as a known gap: an operator
investigating a specific incident currently has to correlate with whether
the LLM/provider was independently known to be up at the time to tell F6
and F7 apart. `docs/runbook.md`'s F7 section documents this explicitly
rather than presenting a distinguishing signal that doesn't exist.

### 8. F8 has no automatic background recovery — a new, minimal `actl sweep` CLI command fills the resulting operational gap

§20's F8 response is "halt, compensate, ask the human for a fresh
mandate." `application.ledger_service.sweep` (§12.2's reservation
sweeper) already exists, is fully tested (`tests/chaos/test_f8.py`), and
correctly force-releases HELD reservations past their TTL with an audited
`reservation.expired` entry — but grepping `src/actl/` for any caller
turns up none: it was never wired into `actl.worker`'s own background
loops (only the webhook and reconciliation pollers are). Without some
operational surface for it, `docs/runbook.md`'s F8 recovery step would
have nothing concrete to tell an operator to run. `actl sweep --ttl-s N`
(`cli.py::_sweep`) is a thin wrapper around the existing, unmodified
`ledger_service.sweep` — zero new business logic, matching every other
CLI subcommand's own shape (`provider-smoke`, `replay-webhook`). Wiring
`sweep` into `actl.worker`'s own background loop, so this stops requiring
a manual or cron-scheduled step at all, is left as a P10-or-later
backlog item — noted explicitly in the runbook rather than silently
addressed by expanding P9's scope into worker-loop changes.

### 9. Golden-trace generation requires an explicit `PAYMENT_PROVIDER=simulator`, or a config-label leaks into the committed fixture

`application.payment_service`'s `payment.intent` audit payload includes
`"provider": settings.payment_provider` — a verbatim echo of the
*configured* provider label, independent of which concrete
`PaymentProvider` object the caller actually passed in (every P9
demo/chaos code path always constructs `SimulatorAdapter` directly,
never through `infrastructure.providers.factory.build_payment_provider`).
The first golden-trace generation run set `LLM_ENABLED=false` but not
`PAYMENT_PROVIDER`, so the committed fixtures initially baked in
`"provider": "razorpay"` (`config.Settings`'s own default) — caught when
`make demo` (which correctly exports `PAYMENT_PROVIDER=simulator`, per
§28 P9 instruction 1's offline-CI requirement) compared a freshly
generated `"provider": "simulator"` trace against it and failed. Fixed by
regenerating the fixtures with both env vars set, and by documenting the
requirement directly in `scripts/generate_demo_golden_traces.py`'s own
usage instructions and `docs/runbook.md`'s prerequisites section, so a
future regeneration doesn't silently reintroduce the same drift.

### 10. `tests/golden` gets its own dedicated, isolated Postgres — never the shared `tests/integration`/`tests/chaos` session container

§28 P9 instruction 5 requires golden traces "stable across reruns" —
which requires audit seq numbers to start at 1 every time the comparison
runs, impossible if `tests/golden` shared `tests/integration/conftest.
py`'s session-scoped container with dozens of other test files that write
to the same chain first. `tests/golden/conftest.py` provides its own
Postgres-only (no Redis needed) session-scoped testcontainer, following
the exact same fixture shape as the wider suite's own precedent, just
scoped to this one directory — `pytest tests/golden` alone (as `make
demo`/`make verify` both run it) always starts from a genuinely empty
chain.

### 11. A golden trace's `seq_range` spans the scenario's full contiguous chain activity, not just entries under the propose-time `trace_id`

The first `export_scenario_trace` implementation scoped each scenario's
exported entries to `get_by_trace_id(propose_trace_id)`. `saga.
complete_purchase` mints its own, fresh `trace_id` for settlement (§22's
correlation model — matching a real, separate checkout/settlement
callback, not the same request that proposed the order), so trace-id
scoping silently omitted exactly the `payment.intent`/`payment.result`/
`settlement.closed` (or `compensation.applied`) evidence a demo scenario
exists to show — caught by inspecting the first generated `stale_price`
fixture and finding no settlement entries in it at all. Fixed by having
`application.demo.run_scenario` capture the audit tail immediately before
and immediately after each scenario's own activity and use that
contiguous `start_seq+1..end_seq` span instead — the same shape `tests/
chaos/test_f{1,6,9,10}.py` already use for "this test's own segment of a
shared chain," here applied to "this whole scenario's own segment,"
wider than any single trace_id.

### 12. `ts` is deliberately excluded from every golden-trace entry

`audit_log.ts` is a Postgres `DEFAULT now()` insert-time value, never
derived from the injected `FrozenClock` — genuine wall-clock time, so it
can never be byte-stable across two separate runs regardless of how
deterministic everything else is. `tests/golden/test_golden_trace.py`
(§28 P3) already established this precedent by never touching `ts` at
all in its own hash-chain fixture; `export_scenario_trace` follows the
same rule for the P9 demo-scenario fixtures.

### 13. `make demo` and `scripts/demo.sh` are two different things, deliberately

`make demo` (offline, CI-safe: `pytest tests/golden` against a fresh,
disposable testcontainers Postgres) satisfies §28 P9 instruction 7's
requirement that it be safely re-runnable and never touch a live
database. `scripts/demo.sh` (live, human-facing: the real `actl demo`
CLI against whichever `DATABASE_URL` `make up`/`make migrate` configured,
closing with `actl verify-chain`) is the "recording script" §28 P9's own
phase objective separately names, matching §20.1's own worked example of
small, clean, ascending seq numbers when run once against a freshly
migrated database. The two were originally one script; splitting them
was forced by discovering that `scripts/demo.sh`'s live CLI run and
`make demo`'s isolated pytest run would otherwise both try to write the
same deterministically-seeded ids into the *same* database on a combined
"`make demo`" invocation, colliding on the second half's own attempt
(`IntegrityError: duplicate key value violates unique constraint
"mandates_pkey"`, caught by actually running `make demo` end to end
before considering P9-7 done). `docs/runbook.md` and `scripts/demo.sh`'s
own header both document the fresh-database prerequisite explicitly.

### 14. Pre-existing `make verify` bug fixed as part of this phase, not deferred

The Makefile's `verify:` target already existed
(`--to $$(uv run python -m actl.cli chain-head)`) but piped `chain-head`'s
entire human-readable line (`head=sha256:... seq=N`) into `verify-chain
--to`, which `argparse(type=int)` would reject outright — a pre-existing
defect, not introduced by P9. Fixing it (`grep -oE 'seq=[0-9]+' | cut -d=
-f2` to extract just the number) was necessary for `docs/runbook.md`'s
F10 section and the "Prerequisites and outputs" section to make a true
claim about what `make verify` actually does; leaving a known-broken
command in a freshly-written operator runbook would have made the
runbook itself unreliable. Confirmed working against the live dev
database (`scanning 445 entries ... CHAIN VALID`) before this ADR was
written.

## Production-readiness corrections (post-review, pre-commit)

### 15. Correction to decision 1: F1 now runs entirely through `handle_order_propose` — the bypass was masking a real bug in it, not a legitimate boundary

Decision 1 argued that going through `handle_order_propose` would make
`STALE_PRICE` unreachable, because it re-derives `intent_hash` from the
*live* catalog version. Production-readiness review asked for an exact
§20 citation proving that bypass mandatory, or for it to be removed. On
re-reading `handle_order_propose` line by line, the "live catalog
version" behaviour was itself the bug, not a P7 security boundary:

`intent_draft.catalog_version` was populated from `live_catalog_version`
(the current, global counter) instead of `quote.catalog_version` (the
value the quote actually pinned) — even though every *other* field on
that same `PurchaseIntent` (`unit_price_minor`, `total_minor`, `nights`,
`refundable`) was already correctly sourced from `quote`, never `live`.
Two independent consequences followed: (a) `domain.policy.rules.
catalog_freshness` (`intent.catalog_version == ctx.catalog_version`)
compared the live counter against itself — vacuously true, so Gate G5's
own policy-engine pre-check could never fail, for any request, ever; and
(b) a legitimate buyer computing `intent_hash` the only way it could —
against its own quote's pinned `catalog_version`, exactly what `tests/
chaos/test_f6.py` and `application.growth.simulation._attempt_purchase`
were already doing on the buyer side — would have that hash rejected as
`INTENT_MISMATCH` the moment the catalog had moved at all, before the
policy engine or the gate ever ran. `STALE_PRICE` was not "unreachable
through this path by design" — it was unreachable through *any* path,
including real HTTP traffic through `interfaces/agent/routes.py`, which
dispatches every `order.propose` message to this exact function.

Fixed with a one-line, root-cause change: `catalog_version=quote.
catalog_version` (see `application/agents/merchant.py`, the surrounding
comment cites this decision). `application/recovery.py` was rewritten to
match — it no longer duplicates gate/policy-engine logic (`_evaluate_
and_gate` is gone); it now plays the buyer's own client role, building
the same `intent_hash` a real buyer would (`_buyer_intent_hash`) and
calling `handle_order_propose` for both the stale attempt and the
retried attempt, exactly like real traffic. The *only* remaining bypass
anywhere in the F1 path is the fault injection itself — the out-of-band
`mutate_price` call — which instruction 1 of the original P9 prompt
requires directly ("F1 must use the required out-of-band price mutation,
not the normal catalog-admin endpoint"). `tests/chaos/test_f1.py` gained
a new, first test, `test_handle_order_propose_itself_detects_stale_price_
after_the_mutation`, calling `handle_order_propose` directly with zero
orchestration wrapper, specifically so a regression of this bug fails
immediately and unambiguously rather than being masked by recovery.py's
own retry logic. All five committed golden traces were regenerated;
`stale_price`'s changed shape (now `actor_type="agent"` on the propose
entries, matching real buyer-agent traffic, where it was previously
`"system"` — an artifact of the bypass) is the visible proof the fix
changed real behaviour, not just internal plumbing.

### 16. Correction to decision 2: `IntegrityHalt` is now durable, cross-process state in Postgres — not an accepted process-local limitation

Decision 2 accepted the in-memory singleton's process-local scope as
"out of scope for this build." Production-readiness review required
persisting the halt using "the existing durable infrastructure specified
by the architecture" instead. §18.1 already establishes Postgres as this
system's sole durable system of record for everything else (mandates,
orders, ledger, audit_log, `catalog_meta`'s own single-row global
counter) — a new `integrity_halt` table (migrations/versions/
0007_integrity_halt.py), single always-present row (`id='default'`,
same precedent as `CatalogMetaRow`), is that same pattern applied here,
not a new kind of infrastructure.

`application/integrity.py` was rewritten: the `IntegrityHalt` class and
`get_integrity_halt()` singleton are gone entirely, replaced by
`IntegrityHalted` (an exception) and `raise_if_halted(uow)` (queries the
durable row via the new `infrastructure.db.repositories.integrity.
IntegrityHaltRepository`). `application.gate.execute_money_action` checks
the same durable row inline (matching its own established never-raises,
typed-`MoneyActionResult` convention rather than the exception used
elsewhere — see decision 17 for the failure-mode this required fixing).
`application.audit_service.verify_chain_and_halt_on_failure` now writes
the trip to that row (`IntegrityHaltRepository.trip`, an atomic,
first-trip-wins `UPDATE ... WHERE halted = false` so a later, possibly
different failure discovered while already halted never overwrites the
original incident's forensic reason/timestamp) instead of calling
`.trip()` on the old in-memory object; callers now commit immediately
afterward, matching every other write in that module.

There remains no `clear()` function anywhere in `src/actl/` — the
instruction's own final bullet ("add a recovery/reset path only if §20
permits it") was checked directly against §20's F10 row and JUDGE SIGNAL
text, which name no compensating or reset action for this failure mode
alone among all ten. Clearing a halt is a direct, manual `UPDATE
integrity_halt SET halted=false, ... WHERE id='default'` an operator runs
against the database itself, documented in `docs/runbook.md`'s F10
section. `tests/chaos/test_f10.py`'s own autouse teardown fixture
performs exactly that same manual statement between tests (never a
convenience method) — it is playing the operator's role for test
isolation, not exercising an application code path, and is called out as
such in the file's own docstring so a future reader does not mistake it
for one.

Proven durable and cross-process, not just "no longer literally the same
Python object," by `tests/chaos/test_f10.py::
test_a_second_fresh_process_also_refuses_work_after_the_halt`: after
tripping the halt from the pytest process, a genuinely separate OS
process (`subprocess.run`, `sys.executable`, `tests/chaos/
_f10_second_process.py`) — a fresh Python interpreter, a fresh SQLAlchemy
engine and connection pool, zero shared Python state, connecting to the
same Postgres for the first time — is shown to refuse the same money
action identically, on its very first request.

"API, worker, demo, and scheduled/sweep entry points must all refuse
money-affecting work while the halt is active" (the instruction's own
wording) is satisfied per named entry point: the HTTP API and `actl demo`
both route through `execute_money_action` already, so decision 16's fix
covers both for free; `actl.worker`'s two loops
(`process_unprocessed_webhooks`, `reconcile_non_terminal_orders`) and
`application.ledger_service.sweep` (the `actl sweep` CLI, §20 F8's own
recovery step) do not route through the gate at all, so each gained its
own `raise_if_halted(uow)` check at its own top — see decision 18 for why
this was scoped to named entry points rather than the single lower-level
`ledger_service._add_movements` choke point every one of them eventually
calls. `tests/chaos/test_f10.py::
test_sweep_and_worker_entry_points_also_refuse_work_after_the_halt`
proves all three raise `IntegrityHalted` once tripped.

### 17. `execute_money_action`'s new halt check had to fail closed on its own infrastructure failure, not just on a confirmed halt

The first version of decision 16's gate.py change queried the durable row
outside the function's existing `try/except Exception: return _deny(...
INTERNAL_ERROR)` block (matching the *old* in-memory check's own
position, which could never raise). `UnitOfWork.__aenter__` calling a
broken `session_factory` can raise, though, and now does reach the
database — `tests/integration/gate/test_gates.py::
test_gate_never_raises_on_unexpected_internal_failure` (a P6-era test
that deliberately passes a `session_factory` that always raises
`RuntimeError`, to prove `execute_money_action` never lets an exception
escape) caught this immediately on the first full-suite regression run
after landing 16: an uncaught `RuntimeError` where a typed `DENY` was
required. Fixed by wrapping the halt-check itself in its own `try/except
Exception: return _deny(ReasonCode.INTERNAL_ERROR, trace)` — unable to
even reach the database to check the halt state is treated as the same
class of infrastructure failure that reason code already covers
elsewhere in this function, not as "assume not halted and proceed" (which
would silently reopen exactly the fail-open gap this whole correction
pass exists to close) and not as `AUDIT_UNAVAILABLE` (reserved for a
*confirmed* halt, so an operator reading a denial reason can still tell
the two situations apart).

### 18. `raise_if_halted` was added at three named entry points, not inside the single shared `ledger_service._add_movements` choke point every money movement (reserve/capture/release/reverse/sweep) already funnels through

`_add_movements` was the more exhaustive, single-point option — gating it
would also cover `saga.complete_purchase`'s S4 capture step and the
worker's webhook-driven capture path, neither of which independently
route through `execute_money_action` today (a real, separate, pre-
existing gap: a saga already `AWAITING_AUTHORIZATION` before a halt trips
can still be captured afterward, unless the caller that resumes it
happens to be one of the three now-guarded entry points). It was not
chosen for this pass: `_add_movements` has no established exception-vs-
typed-result convention of its own (each of its five callers has a
different return shape), and routing `IntegrityHalted` up through
`saga.complete_purchase`'s S3/S4/S5 step machinery without auditing every
retry/idempotency path it touches first risked destabilizing already-
tested P6 saga behaviour under time pressure — a correctness regression
in service of a different correctness fix. The three entry points named
explicitly in the instruction (API/demo via the gate, `actl.worker`'s two
loops, `actl sweep`) are covered with a small, auditable, easily-reverted
change each; closing the `saga.complete_purchase` gap identified here is
left as a named follow-up, not silently declared out of scope.

### 19. `make demo` prints an explicit six-row PASS/FAIL summary, and the sixth row gets a real, independently-verified bundle path

Decision 13 already split `make demo` (offline, isolated, `pytest tests/
golden`) from `scripts/demo.sh` (live CLI narration) for re-runnability.
Neither produced the specific output the instruction asked for: the exact
six §20.1 names each with its own PASS/FAIL and evidence path, verified
offline. `scripts/run_demo_suite.py` is the new, dedicated script `make
demo` now calls: it runs the five named scenarios in §20.1's own order
against its own fresh, disposable testcontainers Postgres (same isolation
as `scripts/generate_demo_golden_traces.py`), compares each against its
committed golden fixture and independently re-verifies that fixture's own
hash chain offline (`application.demo.verify_trace_offline`, factored out
of `tests/golden/test_demo_golden_traces.py` so the two share one
implementation rather than two that could drift), then exports the whole
five-scenario chain as an offline audit bundle via the existing, unmodified
`scripts/export_audit_bundle.py` (the "existing audit-chain/export
verifier" instruction 5 of the original P9 prompt already named) and
independently verifies *that* bundle by running its own generated,
dependency-free `verify_bundle.py` as a genuinely separate subprocess —
the sixth, closing row, printed with the bundle's own `audit_log.ndjson`
path, matching §20.1's own closing `actl verify-chain` step. `tests/unit/
application/test_demo.py` pins the five-name constant (`SCENARIOS ==
("happy_path", "over_cap", "stale_price", "declined", "llm_down")`)
against literal §20.1 text and cross-checks it against the CLI's own
`--scenario` argparse choices, so the registered set and what §20.1
documents can never silently drift apart.

### 20. Correction to decisions 6/13/19: `verify_chain` is now a full, golden-traced member of the registered demo-item set — closing a real five-vs-six coverage asymmetry, not a change to §20.1's literal text

§20.1's own literal text is unchanged by this correction and was re-read
character by character to confirm it: five `actl demo --scenario <name>`
invocations, then a sixth, differently-shaped, separate command, `actl
verify-chain --from 1 --to 80` — the section's own closing line calls
them "those six **commands**," never "six scenarios." Decision 6 was
correct that `--scenario` never takes a sixth literal value, and nothing
here changes `SCENARIOS` or the CLI's `--scenario` choices — `actl demo
--scenario verify_chain` is still rejected (`tests/unit/application/
test_demo.py::test_cli_does_not_accept_verify_chain_as_a_scenario_value`).

What decisions 13/19 got wrong was treating the sixth command as
second-class once it reached `make demo`/`make verify`: rows 1–5 each
compared a freshly generated trace against a *committed* golden fixture,
byte-for-byte, every run; the sixth row (labelled `verify-chain`)
exported a *fresh, uncommitted* bundle every time and only checked it
against itself, never against a stable, committed reference — a real
verification-strength gap between item 6 and items 1–5, not a cosmetic
one. Flagged directly: "the current report claims six demo rows but only
five golden traces/bundle checks and a five-name set."

Fixed by formalising the sixth command as `verify_chain`, a full member
of a new `application.demo.DEMO_ITEMS = (*SCENARIOS, "verify_chain")` —
six items, not five-plus-an-afterthought. `application.demo.
export_chain_trace` gives it the same canonical trace shape the five
scenarios already have (`_export_entries`, factored out of `export_
scenario_trace` so both share one implementation), spanning the *whole*
assembled chain (seq 1..N) rather than one scenario's own segment, since
the six §20.1 commands are meant to run once, in order, against one
freshly migrated database — seq 1..N after all five scenarios *is*
`verify_chain`'s own "trace." `fixtures/golden_traces/demo_verify_chain.
json` is now committed alongside the other five (27 entries — the exact
sum of the five scenarios' own 6+2+8+5+6), compared byte-for-byte and
offline-verified with the identical `verify_trace_offline` function used
for the other five. The independent bundle-export-and-subprocess-verify
kept from decision 19 is retained as an *additional* check specific to
this one item (real value: a second, structurally different verification
path using the existing, unmodified `scripts/export_audit_bundle.py`),
not the row's only evidence.

`make verify` now runs `test_all_six_golden_fixture_files_are_present`
(new — explicit, named failure if any of the six fixture files is
missing, rather than relying on an incidental `FileNotFoundError` from a
later comparison) ahead of the offline-verify test, both now parametrized
over `DEMO_ITEMS` (six cases each) instead of `SCENARIOS` (five).
Confirmed by deliberately removing `demo_verify_chain.json` and re-running
`make verify`: it fails immediately and by name
(`test_all_six_golden_fixture_files_are_present FAILED`), restored before
committing anything.
