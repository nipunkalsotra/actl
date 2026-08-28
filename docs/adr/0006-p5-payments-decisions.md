# 0006 — P5 payments, webhooks, and reconciliation decisions

Status: Accepted
Date: 2026-08-29

## Context

P5 ("Payments adapter, webhooks and reconciliation", §28) implements §15:
the `PaymentProvider` port, a real Razorpay test-mode adapter, a
deterministic simulator, idempotent order creation, signature-gated
capture, webhook receipt/processing, and reconciliation. Building this
phase required (a) verifying Razorpay's current API against its own live
documentation rather than memory, per the phase's explicit instruction,
and (b) reconciling several places where §15 states a principle without
specifying the exact mechanics. Two genuine concurrency bugs were found
and fixed while writing the test suite; both are recorded here alongside
the interpretive decisions, per this project's standing practice.

## Razorpay documentation consulted (current, verified live during this phase)

- Orders API overview: https://razorpay.com/docs/payments/orders/apis/
- Orders API — create: https://razorpay.com/docs/api/orders/create/
  (`POST /v1/orders`; request `amount`/`currency`/`receipt`/`notes`;
  response `id`/`entity`/`amount`/`amount_paid`/`amount_due`/`currency`/
  `receipt`/`status`/`attempts`/`notes`/`created_at`)
- Payments API overview: https://razorpay.com/docs/api/payments/
- Payments API — fetch by id: https://razorpay.com/docs/api/payments/fetch-with-id/
- Payments API — fetch for an order:
  https://razorpay.com/docs/api/payments/fetch-payments-orders/
  (`GET /v1/orders/:id/payments`, collection response `{entity, count, items}`)
- Payments API — capture: https://razorpay.com/docs/api/payments/capture/
  (`POST /v1/payments/:id/capture`, request `amount`/`currency`)
- Authentication: https://razorpay.com/docs/api/authentication/
  (HTTP Basic Auth, `key_id:key_secret` base64-encoded)
- Webhook validation: https://razorpay.com/docs/webhooks/validate-test/
  (`X-Razorpay-Signature`, HMAC-SHA256 of the *raw* body, hex digest;
  test-mode webhooks fire on real test-mode transactions)
- Webhook payload shapes: https://razorpay.com/docs/webhooks/payments/
  (`payment.captured`/`payment.failed` envelope:
  `{entity, account_id, event, contains, payload, created_at}`, nested
  `payload.payment.entity`)
- Webhook FAQs / dedup and retry: https://razorpay.com/docs/webhooks/faqs/
  (`X-Razorpay-Event-Id` header is unique per delivery; non-2xx responses
  retry on an exponential backoff for 24h, then the webhook is disabled)
- Checkout signature verification (via search of Razorpay's own docs and
  SDK reference — `razorpay-node/documents/paymentVerfication.md`):
  `hmac_sha256(order_id + "|" + razorpay_payment_id, key_secret)`, matches
  §15.4 exactly
- Test-mode API keys: https://razorpay.com/docs/payments/dashboard/account-settings/api-keys/
  (Dashboard → Account & Settings → API Keys, Test/Live mode toggle,
  `rzp_test_` prefix)

## Decisions

### 1. `application/payment_service.py`, not `application/gate.py`

§11.2's reference implementation names `gate.py` as "the ONLY module
permitted to import a payment provider" — but that file is the full
seven-gate Money Action Gate (mandate re-validation, budget reservation,
quote freshness, idempotency, write-ahead audit), which is explicitly
P6's deliverable ("seven gates, ledger reservations, saga"), not P5's.
`payment_service.py` implements exactly P5's scope — the S2–S5 saga steps
(ORDER, AUTHORIZE, CAPTURE, webhook/reconciliation SETTLE) — and depends
*only* on the `PaymentProvider` Protocol (`application/ports.py`),
injected as a parameter, never importing a concrete adapter. This
satisfies the import-linter contract's "Only the gate may reach a payment
provider" by construction, not by relying on the contract's current
`source_modules` list happening not to name this new module. When P6
builds the real gate, it calls into `payment_service.py`'s functions the
same way; no payment code needs to move.

### 2. Idempotency key transported via Razorpay's `receipt` field

§15.2 says "Sent to the provider as the Idempotency-Key / receipt field on
order creation" — the verified Orders API has no separate
request-level idempotency-key parameter, only `receipt` (documented as a
unique-per-account reference, ≤40 ASCII characters). The derived key
(`"ik_" + 32 hex chars` = 35 characters) fits. `RazorpayAdapter.create_order`
sends it as `receipt`.

### 3. `httpx2`, not the official `razorpay` Python SDK

The official SDK is synchronous (built on `requests`); this codebase is
async end to end (async SQLAlchemy, FastAPI, asyncio worker loops).
Wrapping every SDK call in `asyncio.to_thread()` would be more code and a
real impedance mismatch for four simple REST calls with Basic Auth.
`httpx2` (this repo's existing async HTTP client, previously test-only)
is promoted to a main dependency instead of adding a second HTTP library.

### 4. `create_provider_order` takes a `session_factory`, not one `uow`

Every other function in `payment_service.py` takes a single, pre-opened
`UnitOfWork` and does one transaction — this one needs two, sequential:
the idempotency claim + local order row + `payment.intent` audit entry
must commit and become durable *before* the external provider call
(§7 step 13, §11.2's G7 "write-ahead audit"); holding a transaction open
across a slow network call would itself be a bug (long-held locks,
connection starvation). The function opens, commits, and closes a fresh
`UnitOfWork` for the pre-call state, makes the external call with no
transaction open, then opens a second `UnitOfWork` to persist the result.

### 5. A bounded poll, not a distributed lock, for idempotency claim losers

§15.2: "zero rows means someone else owns this attempt." A losing caller
needs the *result* of the winner's attempt, not just the fact that it
lost. No pub/sub or distributed-lock mechanism exists in this build, so
the loser polls `idempotency_keys` every 50ms for up to 2s, then returns
the winner's stored order. This is sufficient for this system's actual
latency profile (a provider round-trip, not a long-running job) and is
proven under real 10-way concurrency (`test_idempotent_retry_creates_one_order`).
A production system with a slower or less-bounded provider might prefer a
different mechanism; nothing here forecloses that.

### 6. SimulatorAdapter's signatures are always real HMAC — never faked

§28 P5 instruction 3 asks for "valid checkout signature" and "deliberately
tampered checkout signature" scenarios. Rather than a scenario flag that
makes `verify_checkout_signature` lie about its own result,
`SimulatorAdapter.build_checkout_payload`/`build_webhook_payload` always
produce a real, correct signature; a "tampered" test mutates the returned
string itself (matching `scripts/tamper.py`'s P3 precedent of mutating
real data, never simulating dishonesty inside the verifier). This means
`verify_checkout_signature`/`verify_webhook` behave identically —
genuinely, not just superficially — between the two adapters.

### 7. Webhook fast-path (HTTP) / worker-path split uses the existing `webhook_events.processed_at` column

§15.3 point 3: "Return 200 within milliseconds. Processing happens on the
worker." The HTTP handler (`process_webhook_delivery`) does only:
verify the signature (in-memory HMAC) and claim-or-detect-duplicate via
one indexed `INSERT ... ON CONFLICT (provider_event_id) DO NOTHING`
(§18.2's `webhook_events` table, unchanged from P2). The worker
(`process_unprocessed_webhooks`) separately polls `WHERE processed_at IS
NULL AND signature_valid` and performs the actual order transition. No
outbox/Redis-stream hop is used for this — `webhook_events.processed_at`
already *is* the queue P2's schema provides; routing through the general
outbox mechanism would duplicate it for no benefit.

### 8. `orders.created_at` is set from the injected Clock, not `server_default=func.now()` — a real bug found

`OrderRepository.add()` previously relied on the column's Postgres-side
default. The reconciler compares `created_at` against `clock.now() -
reconcile_after_s` using an *injected* Clock (so tests can control it with
`FrozenClock`). A DB-side wall-clock timestamp desyncs from any injected
clock — found when `test_missing_webhook_recovered_by_reconciler`
initially returned zero results despite advancing a `FrozenClock` by 100
seconds: the order's real `created_at` was always "in the future" relative
to the frozen clock's frozen-at-the-past `now()`. Fixed by having
`create_provider_order` pass `created_at=clock.now()` explicitly, and
`OrderRepository.add()` honour it when supplied.

### 9. `CircuitOpenError` is caught alongside `TransientProviderError`/`RetryExhausted` at every call site — a real bug found

`CircuitBreaker.call()` raises `CircuitOpenError` when the circuit is
open — a sibling of `TransientProviderError`/`TerminalProviderError`
under `ExternalServiceError` (platform/errors.py), not a subclass of
either. The three call sites that catch provider failures (order
creation, capture, reconciliation's `fetch_payments` poll) originally
caught only `(RetryExhausted, TerminalProviderError)`, so a tripped
breaker propagated as an *uncaught* exception instead of being classified
— in the reconciler this crashed the whole reconciliation pass (all
remaining orders in that tick, not just the one that tripped it), not just
the one order whose poll failed. Found via a combined-suite test-order
flake (`test_reconciler_poll_failure_is_transient_and_leaves_order_non_terminal`
sweeping up other tests' leftover non-terminal orders and tripping a fake
provider's breaker after 5). Fixed by adding `CircuitOpenError` to all
three except clauses: an open breaker is itself a transient condition (it
recovers after `recovery_timeout`), and for the reconciler specifically it
must never be treated as evidence that a *specific* order was declined.

### 10. The idempotency "existing key found" path had a duplicate-order race — a second real bug found

`create_provider_order`'s original `_replay_existing` helper handled
`state == "COMPLETED"` and `state == "FAILED"` correctly, but for
`state == "IN_FLIGHT"` (found on the *initial* check, before this caller
ever attempted `claim()`) it fell through to "read the order row and
return it if present" — except the order row is created in the winner's
*first* transaction, before `provider_order_id` is set (that lands in the
winner's *second* transaction). A caller landing on this branch could
return a real order with `provider_order_id=None`, observably a duplicate
attempt with no provider order at all — reproduced directly by a 10-way
concurrent test run inside a wider combined session (~1 in 20–30 runs).
Fixed by removing `_replay_existing` and inlining the three states
directly in `create_provider_order`: `COMPLETED` returns the stored
order, `FAILED` raises `IdempotentAttemptFailed`, and `IN_FLIGHT` — found
on either the initial check *or* a lost `claim()` race — goes through the
same bounded poll (decision 5), never a naive early return.

### 11. [CORRECTED] The live smoke test requires an explicit opt-in flag — credentials present is never sufficient on its own

The opt-in smoke test (`tests/integration/payments/test_razorpay_live_smoke.py`)
must skip unless a real network call is actually wanted. The original
version of this decision gated only on credential *shape*: skip unless
`RAZORPAY_KEY_ID`/`RAZORPAY_KEY_SECRET` looked real (rejecting both
`config.py`'s own placeholder default and `.env.example`'s literal
template text, `rzp_test_xxxxxxxxxxxxx`). That check was a genuine
improvement over checking only the one hardcoded placeholder string, but
it was still an insufficient *gate*: a developer's `.env` legitimately
carries real test-mode credentials for other reasons (the
`provider-smoke` CLI command, manual demos), and this test ran the moment
those credentials existed — with no separate signal that a real network
call was actually intended for *this* run. `pytest
tests/integration/payments -q` alone was enough to trigger it.

The corrected gate adds a fourth, independent condition that must be set
explicitly and is never implied by anything else: the environment flag
`RUN_RAZORPAY_LIVE_SMOKE=1`. The full skip condition is now: the flag is
set, `PAYMENT_PROVIDER=razorpay` (or its default, which already is
`"razorpay"` — a simulator-configured run never reaches this regardless),
and both credentials are real by the existing shape check. The test is
also marked `@pytest.mark.real_provider` (registered in
`pyproject.toml`), so it can be identified or filtered independently of
the skip condition — a second, orthogonal way to keep it out of a normal
run. `make test` never reaches `tests/integration/payments` at all
(unit/property/architecture only); CI (`.github/workflows/ci.yml`) runs
that same restricted set and never sets `RUN_RAZORPAY_LIVE_SMOKE`, so both
are fully offline regardless of what credentials happen to be configured
anywhere in the environment.

### 12. `orders.provider_payment_id`/`orders.decline_reason` — migration 0004

Not in §18.2's excerpt (written before P4/P5 were scoped). One
`provider_order_id` can carry more than one payment attempt; capture and
reconciliation need to know which `provider_payment_id` is authoritative.
`decline_reason` carries the provider's own error code for a terminal
failure — never raw card data or a signature (`RazorpayAdapter._safe_error_body`
already strips Razorpay's own error responses to `{code, description}`
before any of it reaches application code or a log line).

### 13. [CORRECTED] A missing, malformed, or invalid signature is rejected with 401 and persists nothing

The original version of this decision argued for always returning 200,
including on an invalid signature, and persisting the event anyway with
`signature_valid=false` "for evidence." A post-P5 review correctly
identified this as a security defect: it accepted and durably stored
attacker-controlled, unauthenticated request bodies before any proof the
request actually came from Razorpay, and it gave a forged or replayed
delivery the same success response as a genuine one — indistinguishable
to anything watching only the HTTP layer.

The corrected behaviour: `process_webhook_delivery` calls
`provider.verify_webhook` (constant-time HMAC-SHA256 over the raw body)
*before any database call whatsoever*. A missing, malformed, or invalid
signature returns immediately — no `webhook_events` row, no outbox row, no
state transition, no worker work — and the HTTP handler responds `401`
with an empty body (never the expected signature, the webhook secret, or
the raw payload). Only a verified-valid signature reaches the durable,
idempotent claim (`INSERT ... ON CONFLICT (provider_event_id) DO
NOTHING`) — a new event gets a fast `200` after that claim commits; a
duplicate delivery of an already-claimed event also gets a fast `200`
with no second claim attempted. If the durable claim itself fails after a
valid signature (a genuine transient database error), the exception is
left to propagate: FastAPI's default handler returns `500` — a non-2xx —
so Razorpay's own retry policy (§15.3's 24h exponential backoff) is what
recovers it, exactly as intended for a real infrastructure failure, as
opposed to a delivery that can never pass. Verification, the HTTP
receiver, and `actl replay-webhook` all go through this one function, so
none of the three callers can silently diverge on the guarantee.

Retry-budget waste is real for a *forged* delivery — a 401 tells an
attacker their forgery was rejected rather than silently accepted, and a
persistent attacker will not be deterred by a 200 either. It is not an
acceptable trade against durably storing unauthenticated request bodies.

### 14. The reconciler only ever *reads* provider state — it never calls `capture()`

`reconcile_non_terminal_orders` calls `provider.fetch_payments()` (read-only)
and reflects whatever status Razorpay already reports (`captured`/
`failed`); it never calls `capture()` itself. This keeps §15.4's "no money
moves without the payer's own authorization" true even for orders the
reconciler recovers — a reconciler-discovered `captured` payment was
captured by Razorpay's auto-capture or by this system's own
signature-gated `verify_and_capture` path at some earlier point the
webhook never confirmed; the reconciler's job is to *notice and record*
that fact, never to *cause* it.

## Consequences

- P6's Money Action Gate calls `payment_service.create_provider_order`/
  `verify_and_capture` directly once it exists — no payment logic needs to
  move out of `application/payment_service.py`, only the mandate/policy/
  budget gates (G1–G7) wrap around it.
- The two concurrency bugs (decisions 9, 10) were caught only because the
  test suite was run repeatedly, combined, and under real 10-way
  concurrency rather than once in isolation — worth preserving that habit
  for P6's saga tests, which will have even more interleaved state.
- A real Razorpay test-mode order was created and its id recorded (see
  the P5 exit-criteria output) — proof the integration is genuine, not
  just internally self-consistent.
