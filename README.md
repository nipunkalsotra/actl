# actl — Agentic Commerce Trust Layer

```
make up && make migrate && make demo
```

Three commands, no credentials, clone to a verified audit chain in under
two minutes. `make demo` runs the six §20.1 scenarios (five fault modes
plus a closing chain verification) against a disposable database and
checks every trace byte-for-byte against committed golden fixtures — see
[Reviewer path](#reviewer-path) below for the timed, from-scratch version.

## Thesis

Autonomous AI buyer agents are starting to place real orders, and the
question a merchant, a payments platform, or a regulator asks first is
never "does it work" — it's "can you prove what it did and why." actl is
a merchant backend an autonomous buyer agent can transact with end to
end, where every money action is bounded by a signed spending mandate,
gated through a single money-action chokepoint before any charge, and
recorded in an append-only, hash-chained audit log that a third party can
verify offline with nothing but Python's standard library. The LLM never
authorizes a purchase — it only extracts intent, ranks candidates, and
narrates outcomes, always behind a deterministic fallback. Bounded,
gated, audited, explainable: each one a property you can watch fail
loudly in `make chaos`, not a claim taken on faith.

Full design: **[`docs/architecture.md`](docs/architecture.md)**. Agent
wire protocol reference: [`docs/protocol.md`](docs/protocol.md).

## Reviewer path

Fresh clone, no existing virtual environment, no local database state, no
`.env`, no Razorpay/Groq credentials — simulator payments and
replay-mode LLM only:

```
scripts/clone_to_demo.sh
```

Prints each step as it runs and reports total clone-to-verified-chain
time at the end. It performs exactly `git clone` (of this repository) →
`make up && make migrate && make demo` in a fresh temporary directory —
see the script for the literal commands if you'd rather run them by hand
against your own already-cloned checkout (skip the `git clone` step in
that case).

## Local development (buyer + merchant UI)

One command starts the real backend (Postgres + Redis, migrated and
seeded) and the buyer + merchant Vite frontend together, with safe local
defaults (`PAYMENT_PROVIDER=simulator`, `LLM_ENABLED=false`,
`ANCHOR_PROVIDER=noop`) — no Docker/migration/seed/port juggling or
multiple terminals to manage by hand:

```
./start.sh
```

```
ACTL is ready
Buyer:    http://localhost:5173/
Merchant: http://localhost:5173/merchant
Backend:  http://127.0.0.1:8000/docs
Logs:     ./logs.sh
Stop:     ./stop.sh
```

- `./status.sh` — compact table: Postgres/Redis health, backend/frontend
  PID + running state + URL health, any conflicting port owner.
- `./logs.sh` (or `./logs.sh backend` / `./logs.sh frontend`) — tails the
  running service(s)' logs.
- `./stop.sh` — stops only the backend/frontend processes this launcher
  started (tracked in the gitignored `.run/`), leaving Postgres/Redis
  running; `./stop.sh --down` also stops those.

Safe by construction: if root `.env` is missing, `start.sh` generates one
from `.env.example` with the three fields above forced and `chmod 600`
— it never invents, reads, prints, or copies a real credential, and
never touches an `.env` you already have. Re-running `./start.sh` is
idempotent — it reuses an already-running ACTL backend/frontend/Compose
stack rather than restarting it, and refuses (without ever killing
anything) if port 8000/5173 is already held by something it didn't
start itself.

**`./start.sh` vs. `scripts/clone_to_demo.sh`** — different jobs:
`./start.sh` is for interactive local development against this checkout,
buyer and merchant UI included, and leaves the stack running afterwards.
`scripts/clone_to_demo.sh` is an isolated reviewer/CI-style verification
run: fresh temporary clone, its own disposable Compose project, no
frontend, torn down automatically at the end — see [Reviewer
path](#reviewer-path) above.

## Setup

Requirements: Docker, `uv`. No `.env` needed for the reviewer path above —
every setting has a safe, test-mode default (`config.py`); copy
`.env.example` to `.env` only if you want to point at a real Razorpay
test-mode account or a real Groq key.

```
make up            # Postgres + Redis
make migrate       # alembic upgrade head
uv run uvicorn actl.main:app --reload   # API on :8000
uv run python -m actl.worker            # background worker (webhooks, reconciliation)
```

`make lint` (ruff + mypy + import-linter contracts), `make test` (unit +
property + architecture fitness tests), `make chaos` (all ten §20
failure modes), `make verify` (live chain + golden-fixture check) are
the same gates CI runs (`.github/workflows/ci.yml`).

## Test-mode safety

This is a 100%-free-tier, test-mode-only build (§01.3). `config.py`
refuses to start if `RAZORPAY_KEY_ID` doesn't begin with `rzp_test_` —
a real live key cannot be configured by accident. The reviewer path never
touches Razorpay or Groq at all: `PAYMENT_PROVIDER=simulator` drives
every payment outcome deterministically, and `LLM_ENABLED=false` (or
`DEMO_REPLAY=true` against committed cassettes) means no network call to
Groq ever happens. The one opt-in exception — a real Razorpay
test-mode smoke test — is excluded from normal CI and only runs when
explicitly enabled (see `tests/` for the marker).

## Demo scenarios

```
uv run python -m actl.cli demo --scenario {happy_path,over_cap,stale_price,declined,llm_down}
uv run python -m actl.cli verify-chain --from 1 --to <seq>
```

The six §20.1 commands, narrated live by `scripts/demo.sh` (`make
record` wraps it for pitch-video capture) or validated automatically by
`make demo` against committed golden traces
(`fixtures/golden_traces/`). Each scenario prints its detected fault,
terminal outcome, recovery/compensation action, reserved balance, and
audit-chain status — proof, not narration: `over_cap` shows zero
provider calls; `declined` shows the exact compensation sequence;
`llm_down` shows the deterministic fallback path with no Groq call.

## Audit verification

```
uv run python -m actl.cli verify-chain --from 1 --to <seq>
make bundle                     # export an offline-verifiable evidence bundle
python3 audit_bundle/verify_bundle.py   # verify it with zero dependencies
```

`verify-chain` recomputes every entry hash and Merkle checkpoint against
the live database. `make bundle` (`scripts/export_audit_bundle.py`)
exports a self-contained directory — NDJSON evidence, checkpoint/Merkle
roots, a file-hash manifest, metadata, and a standalone verifier with the
canonicalisation/hashing logic copied verbatim, not reimplemented — that
a third party can check with no database, no network, no `actl`
installed, and no secret, from a completely isolated directory
(`tests/integration/audit/test_export_bundle.py` proves this claim
in a subprocess with `actl` deliberately unimportable). Tamper tests
cover every evidence artifact: a corrupted file is caught by the
manifest before any chain logic runs; a consistently-forged file (data
and manifest hash both changed) is still caught by the entry-hash chain,
Merkle root, or head-hash check.

## Optional: Monad Testnet anchoring

`NoopAnchor` is the default (byte-for-byte the P3 behaviour) — nothing
below runs unless you explicitly set `ANCHOR_PROVIDER=monad`. When
enabled, an async worker loop publishes checkpoint Merkle roots — and
only the roots, never business data — to a Monad Testnet contract, as
external timestamping evidence for the offline-verifiable hash chain
above. Fully optional, fully asynchronous, never on the money/audit-append
path: a Monad outage can never block a purchase, ledger action, or audit
append. Full docs, deployment steps, and failure/retry behaviour:
[`docs/monad-testnet.md`](docs/monad-testnet.md); design rationale:
[ADR 0016](docs/adr/0016-p11-monad-anchoring-decisions.md); a real,
deployed-contract proof on live Testnet:
[live Testnet proof](docs/monad-testnet.md#live-monad-testnet-proof).

## Observability

```
curl -s localhost:8000/metrics | grep actl_
curl -s -H "Authorization: Bearer $READ_TOKEN" localhost:8000/audit/explain/{order_id}
```

- **Traces.** OpenTelemetry spans around every application use case,
  Money Action Gate check, saga step/compensation, and external call
  (provider, LLM). The OpenTelemetry trace id is the *exact same*
  128-bit value as the audit chain's own `trace_id` — not merely
  correlated, a lossless round trip (`platform/tracing.py`,
  `tests/integration/observability/test_tracing_otel.py` proves the
  equality against real persisted audit rows). No exporter is wired up
  by default (spans are created and dropped — a genuinely free-tier,
  no-collector build); pass an exporter to `configure_tracing()` to
  collect them.
- **Metrics.** Prometheus text at `/metrics`: decisions by verdict and
  reason code, gate denials by gate, compensations, LLM calls and cache
  hits, audit chain length, reconciliation lag, plus RED (rate/errors/
  duration) per HTTP route. Every label is drawn from a small, closed
  set — never an order id, mandate id, user text, SKU, trace id, or
  provider id (`tests/unit/platform/test_metrics.py` asserts this by
  construction).
- **Explain endpoint.** `GET /audit/explain/{order_id}` (Read token
  auth) returns the ordered causal timeline — mandate, quote, decision
  with rule trace, gate outcomes, provider events, webhook receipt,
  compensation, and settlement — each item's hashes and trace id, fact/
  decision/provider-event/compensation clearly distinguished, never a
  secret or raw webhook body.
- **Logs.** Structured JSON with `trace_id`, redacted by a filter, not
  discipline. `tests/integration/observability/test_secret_redaction.py`
  injects a unique canary into every secret-bearing setting and proves
  none of them reach a log line, span, metric, or API response across a
  full transaction — including the LLM-fallback and compensation paths.

## Limitations

- **Frontend is out of scope** by design (§01 SCOPE) — this is a
  backend/domain/infrastructure build; a reviewer interacts with it via
  `curl`/the CLI, not a UI.
- **No generic outbox relay or DLQ drainer** are built — audit
  checkpointing happens synchronously in the same transaction as the
  entry that crosses a checkpoint boundary, which is what makes the
  "persist atomically" guarantee trivially true rather than something a
  second process has to get right. Optional Monad Testnet anchoring (§28
  P11) is its own async worker loop using `audit_checkpoints` itself as
  the outbox — see [Optional: Monad Testnet anchoring](#optional-monad-testnet-anchoring)
  below; `NoopAnchor` (the `Anchor` port's default) remains a true no-op.
- **`catalog.queried` audit entries carry no mandate/order/quote
  linkage** in their subject (a buyer can browse before choosing
  anything), so `GET /audit/explain/{order_id}` cannot correlate a
  catalog read back to a specific order without a broader session/cart-id
  design change this build doesn't make.
- **Mandate issuance is out of this merchant-side build's scope** — a
  mandate arrives already locked and signed from the buyer-agent's own
  system; there is no create-mandate flow here to audit, so the explain
  endpoint's `mandate.locked` timeline entry is synthesized from the
  mandate row's own ingestion timestamp, not a dedicated audit-chain
  write.

## Security notes

- Every secret-bearing setting (`RAZORPAY_KEY_SECRET`,
  `RAZORPAY_WEBHOOK_SECRET`, `GROQ_API_KEY`, `QUOTE_SIGNING_KEY`,
  `MANDATE_SIGNING_KEY`, `ADMIN_TOKEN`, `READ_TOKEN`,
  `MERCHANT_PRIVATE_KEY_HEX`) has a placeholder test-mode default and is
  redacted by a key-pattern filter before any structured log line is
  rendered (`platform/redaction.py`) — proven, not assumed, by the
  canary-injection test above.
- Two separate bearer-token tiers: `ADMIN_TOKEN` (demo-only catalog price
  mutation, `POST /admin/catalog/{sku}/price`) and `READ_TOKEN` (read-only
  audit explain, `GET /audit/explain/{order_id}`) — a reviewer/dashboard
  credential can never also mutate the catalog.
- `capture()` is unreachable unless the Razorpay Checkout signature
  verifies; a tampered signature yields `PROVIDER_DECLINED` and a
  compensated saga, never a charge (`tests/integration/payments/
  test_checkout_signature.py`).
- The LLM never authorizes a purchase (§02, ADR
  [`0012-llm-never-authorizes`](docs/adr/0012-llm-never-authorizes.md));
  every extraction/ranking/narration call is schema-validated,
  budget-capped at three calls per transaction, and behind a
  deterministic fallback that the money path never blocks on.
- Agent-to-agent messages are Ed25519-signed and replay-protected
  (`§14.1`); HMAC-SHA256 "development fallback" signing is refused
  outside pytest by a startup check. Full protocol reference, including
  the verification pipeline and the merchant mandate-hash trust
  boundary: [`docs/protocol.md`](docs/protocol.md).

## Status

**P0–P10 complete.** Every phase in the roadmap below is implemented and
green:

- **P0** Foundation and rails — config, platform primitives, import-linter contracts.
- **P1** Domain core — mandate model, canonical JSON, the policy engine.
- **P2** Persistence — schema, repositories, transactional outbox.
- **P3** Trust layer — append-only hash chain, Merkle checkpoints, verifier.
- **P4** Catalog, agent feed and price locks.
- **P5** Payments adapter, webhooks and reconciliation.
- **P6** Money Action Gate, ledger and saga.
- **P7** Agent protocol and the two agents (signed agent-to-agent commerce).
- **P8** LLM layer — Groq, guardrails, deterministic fallback.
- **P9** Failure theatre — deterministic fault injection for all ten §20
  failure modes (`tests/chaos/`), the six §20.1 demo scenarios
  (`actl demo --scenario`), committed golden traces, and the operator
  runbook.
- **P10** Observability and release — OpenTelemetry traces with exact
  audit-trace-id equality, Prometheus metrics, `GET /audit/explain/
  {order_id}`, secret-redaction proof, the completed offline audit
  bundle exporter, and this reviewer path.
- **P11** Optional Monad Testnet anchoring — an owner-controlled
  `AuditCheckpointAnchor` contract, a keystore-based deployment flow, an
  async/idempotent/non-blocking worker loop, and the opt-in on-chain
  verifier — `NoopAnchor` remains the default, byte-for-byte unchanged.

See `docs/architecture.md` §28 for the full phase roadmap and exit
criteria, and [`docs/runbook.md`](docs/runbook.md) for the detection,
containment, recovery and escalation steps for each of the ten failure
modes.
