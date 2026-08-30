# 0015 — Two payment adapters behind one port

Status: Accepted
Date: 2026-08-30

## Context

Every money-path test, chaos scenario (§20), and demo run needs to
deterministically produce specific payment outcomes — a decline, a
timeout, a signature mismatch, a slow provider — on demand, and CI has
to run without ever placing a real charge against a real Razorpay
account. At the same time, the whole point of the build is that it
actually integrates with a real payment provider in test mode, not just
simulates one. The architecture has to decide how both are true at once
without the money-critical code path itself knowing which one it's
talking to.

## Decision

`application/ports.py` defines one `PaymentProvider` protocol
(`create_order`, `capture`, `verify_checkout_signature`, `fetch_payments`,
`refund`, `verify_webhook`), and two concrete implementations satisfy it:
`infrastructure/providers/razorpay/adapter.py` (the real Razorpay test-mode
SDK/HTTP client) and `infrastructure/providers/simulator/adapter.py` (a
deterministic in-process fake with named `Scenario`s — declined,
transient-then-succeeds, timeout — and the same checkout/webhook payload
shapes the real adapter produces). `application/gate.py` and
`application/payment_service.py` depend only on the `PaymentProvider`
port; an import-linter contract (§21, §23.4:
`test_only_gate_imports_payment_provider`) makes "only the gate (or the
provider factory) may import the concrete Razorpay adapter" a CI-enforced
fact, not a convention — the same test is "demonstrably red if any other
module imports the adapter" (§30's own "Gated" bar). `PAYMENT_PROVIDER`
(env var) selects which adapter `infrastructure/providers/factory.py`
constructs; every automated test, `make chaos`, and `make demo` force
`PAYMENT_PROVIDER=simulator` regardless of `.env`.

## Consequences

- **Every fault scenario is exactly reproducible.** `SimulatorAdapter`'s
  named scenarios turn "a payment gets declined" or "a provider call
  times out then a retry succeeds" from a hard-to-trigger real-world
  condition into a one-line, deterministic test fixture — which is what
  makes the golden-trace fixtures (§28 P9) byte-stable across reruns and
  across machines at all.
- **CI never places a real charge.** No test in the normal suite ever
  constructs the Razorpay adapter; the one real-Razorpay smoke test is
  excluded by default and opt-in only, so a CI credential leak or a bug
  in test selection can't accidentally hit a live-adjacent endpoint.
- **The gate and saga code that actually moves money is identical in
  every test and in production** — a test proving G6/G7/the saga's
  compensation paths behave correctly against the simulator is a test of
  the exact same code that runs against real Razorpay, not a separate
  code path that could silently diverge.

## Alternatives considered

- **Mock the HTTP layer of a single Razorpay adapter** (e.g. `respx`/
  `httpx` mocking) instead of a real second adapter implementing the same
  port. Rejected: an HTTP-level mock still has to reimplement Razorpay's
  exact response shapes and would couple every test to that HTTP
  contract's incidental details (headers, exact error envelopes) rather
  than to the `PaymentProvider` port's own semantic contract — a second,
  genuine adapter is what lets `SimulatorAdapter` also drive `verify_
  checkout_signature`/`build_checkout_payload` with the *same* signing
  scheme the real flow uses, not a stubbed-out response.
- **One provider, real Razorpay only, with CI using recorded
  cassettes.** Rejected: cassette replay can prove "we sent this request
  once and got this response," but can't deterministically produce
  *every* named fault scenario §20 requires on demand, and ties every
  test run to a fixture that goes stale the moment Razorpay's API shape
  changes.

## Relevant architecture section

§15 Payments adapter — provider port and two adapters; §21 Module
boundaries (`only the gate may reach a payment provider`); §23.4
Architectural fitness functions.
