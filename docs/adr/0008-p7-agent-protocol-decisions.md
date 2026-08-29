# 0008 — P7 signed agent-to-agent protocol decisions

Status: Accepted
Date: 2026-08-29

## Context

P7 ("signed agent-to-agent protocol", §28) implements §8.4's AgentEnvelope
and §14's seven message types end to end: Ed25519 signing/verification,
an agent-identity registry, Redis-backed replay/freshness protection, the
merchant-agent security boundary, and a pure buyer-agent candidate
filter/ranker. `LLM_ENABLED=false` throughout — no Groq call exists in the
codebase yet. Several places required an interpretive or corrective
decision; they are recorded here per this project's standing practice (see
ADR 0006/0007 for the P5/P6 precedent). Decisions 6 and 8 are a follow-up
correction pass after the first delivery: decision 6 tightens the HMAC
envelope fallback from "present but never exercised" to "explicitly gated
and rejected at startup outside tests," and decision 8 closes a reporting
gap where the first delivery's summary did not name `error` as the seventh
§14 message type even though it was already implemented and tested.

## Decisions

### 1. A single dispatch endpoint, `POST /agent/v1/messages`, not a wrapper around the existing P4 routes

ADR 0005's own consequence 2 speculated that "P7's envelope layer wraps
`GET /agent/v1/catalog`/`POST /agent/v1/quote` without changing their
bodies." Building it, that shape does not fit: §14's envelope is one
signed request/response pair per message, and `catalog.query`/
`quote.request` are two of *seven* message types that must all go through
the identical verify→identity→replay→timestamp pipeline before any
business handling runs (§28 P7 instruction 4). Wrapping two existing REST
endpoints individually would mean either duplicating that pipeline five
more times (for `capability.discover`, `order.propose`, `order.status`,
`receipt.issue`, and error) or inventing five more routes with no REST
shape to wrap.

`interfaces/agent/routes.py` instead exposes one endpoint,
`POST /agent/v1/messages`, that discriminates on `envelope.type` after the
shared pipeline runs, and dispatches to per-type handlers in
`application/agents/merchant.py`. Those handlers call the *same*
`catalog_service.list_catalog`/`create_quote` functions P4's routes call —
so the bodies are unchanged, as ADR 0005 anticipated — but the transport is
one endpoint, not two wrapped ones. P4's `GET /agent/v1/catalog`/
`POST /agent/v1/quote` are untouched and still plain typed REST (ADR 0005
decision 11 stands); `POST /agent/v1/messages` is new, additive surface
alongside them, matching Appendix A's "Signed envelope" column for the
agent-protocol paths without changing P4's own auth story.

### 2. Response envelopes are signed; protocol-layer rejections are plain JSON

A successful or business-rejected message gets a full signed
`AgentEnvelope` back, addressed to the verified sender, signed with the
merchant's own Ed25519 identity (`settings.merchant_*`). But an envelope
that fails verification itself — malformed encoding, unknown protocol
version, unknown algorithm, unknown/revoked/expired key id, bad
signature, replay, clock skew — has, by definition, no *verified* sender
identity to address a signed response to; signing a response "to" an
identity whose own signature just failed would be a stronger authenticity
claim than the situation warrants. Those rejections get a plain, unsigned
JSON error body instead (`_plain_error` in `routes.py`), with a distinct
HTTP status per rejection reason (`_REJECTION_STATUS`: 400 for malformed/
unknown-version/unknown-algorithm/clock-skew, 401 for unknown-identity/
bad-signature, 403 for revoked/expired identity, 409 for replay, 503 for
replay-check-unavailable).

Every *business* outcome — including an `order.propose` rejection such as
`MANDATE_TAMPERED`/`INTENT_MISMATCH`/`STALE_PRICE` — is HTTP 200 with a
typed, signed envelope body carrying the closed `ReasonCode`. This matches
"produce only the protocol errors/statuses the architecture defines"
(§28 P7 instruction 4): the protocol layer's status codes are fixed and
small; business rejection is data inside an otherwise-successful protocol
exchange, not a second HTTP-status vocabulary layered on top of it.

### 3. The merchant never receives, parses, or trusts a buyer-supplied mandate body

§28 P7 instruction 5's hard boundary. `OrderProposeBody` (§14) accepts
only `quote_id`, `quote_hash`, `mandate_id`, `mandate_spec_hash`, and
`intent_hash` — identifiers and hashes, never a mandate document.
`handle_order_propose` loads the quote (by `quote_id`) and the mandate (by
`mandate_id`) from the merchant's own database, reconstructs the
`PurchaseIntent` from those loaded records plus the live catalog item, and
computes `intent_hash`/compares `mandate_spec_hash` itself — the buyer's
claimed values are only ever compared against, never assigned into
anything the policy gate or ledger later reads.
`tests/integration/agents/test_merchant_boundary.py` proves the negative
directly: an envelope carrying an altered `mandate_spec_hash` (or an
`intent_hash` computed from tampered terms) is rejected
`MANDATE_TAMPERED`/`INTENT_MISMATCH` before the gate ever runs, and no
code path exists that could parse a mandate body from the request even if
one were sent — `OrderProposeBody` has no field for it.

### 4. Migration 0006 does not recreate `ix_audit_log_subject_order_id`

`migrations/versions/0002_audit_outbox.py` already creates this index
(verified via `git diff --stat` against the committed P2 migration, and by
a real `DuplicateTableError` the first time 0006 was drafted with a
duplicate `CREATE INDEX`). Migration 0006 only adds `agent_identities` and
its own indexes; the comment in `infrastructure/db/repositories/
audit_log.py`'s `get_seq_range_for_order` correctly attributes that index
to P2, not P7.

### 5. NUL-byte rejection added at every boundary where an attacker-controlled string reaches SQL

Not a spec decision — a real bug Schemathesis found (§28 P7 instruction 8
exists precisely to catch this class of defect). Hypothesis generated a
`category` query value containing an embedded `\x00`; Postgres/asyncpg
cannot represent a NUL byte in a text value at all
(`CharacterNotInRepertoireError`), so it reached `GET /agent/v1/catalog`'s
`WHERE` clause and crashed as an unhandled 500 — a genuine, reproducible
contract violation (not test flakiness), failing `not_a_server_error` on
roughly 1 run in 5.

Fixed by rejecting at the earliest Pydantic parse boundary,
`pattern=r"^[^\x00]*$"` on every string field that can reach a SQL clause
downstream: `domain/agent/envelope.py`'s `AgentEnvelope`/`SignatureBlock`
(`sig.key_id` flows into `agent_identities` lookups, every field flows
into the nonce cache key or audit payloads), `interfaces/agent/schemas.py`'s
seven message bodies, and the pre-existing P4 `interfaces/http/routers/
catalog.py`'s `category`/`location`/`cursor` query params and
`QuoteRequest.sku`/`.mandate_id`. This is a narrow input-validation fix at
a trust boundary, not a P0-P6 behaviour change for any valid input — every
P0-P6 test still passes unmodified — and it is required to make the
Schemathesis suite the user explicitly asked for actually deterministic,
per instruction 8's own wording ("ensure tests are deterministic"). Stable
across 5 repeated targeted runs plus 2 full-suite runs after the fix.

### 6. Ed25519 is the only algorithm `verify_envelope` accepts in any normal runtime — the HMAC-SHA256 fallback is gated, not just quietly present

§14.1 documents HMAC-SHA256 as agent-protocol's "development fallback,"
and `sign_envelope_hmac`/`verify_envelope_hmac` exist in
`domain/agent/envelope.py` alongside the Ed25519 pair — but the first
version of this phase accepted an HMAC-signed envelope from any identity
registered `alg="HMAC-SHA256"` unconditionally, with no runtime switch at
all. That is a real signing-strength downgrade risk: the only thing
preventing an HMAC-mode identity from authenticating production traffic
was that nothing had ever inserted one, not that the code refused to
honour one if it existed.

Corrected: `Settings.agent_envelope_hmac_test_only` (default `False`) is
now the sole switch, and `config._enforce_no_hmac_outside_pytest` — run at
import time alongside `_enforce_test_mode` — raises `SystemExit` if it is
ever `True` while `PYTEST_VERSION` is absent from `os.environ`.
`PYTEST_VERSION` is set by pytest itself for the whole session (pytest ≥
7.2), never by application configuration, so no real `.env` or production
settings file can satisfy it — only actually running under pytest can.
`envelope_service.verify_envelope` checks the flag immediately after the
"known algorithm" check and rejects any `HMAC-SHA256`-signed envelope with
`UNKNOWN_ALGORITHM` when it is off — before identity resolution, so an
HMAC-mode identity accidentally present in the registry still cannot
authenticate anything outside test-only configuration.
`domain/agent/envelope.py`'s `SUPPORTED_ALGORITHMS`/`is_known_algorithm`
are unchanged (both algorithms are still ones this envelope *shape* knows
how to sign/verify, matching §14.1's own text) — the narrowing is a
runtime-policy decision in the application layer, not a domain-purity
question. `tests/unit/test_config_guard.py` proves the startup abort (and
that it boots fine under pytest, and that the flag defaults off);
`tests/integration/agents/test_hmac_disabled.py` proves an HMAC-signed
envelope from a genuinely HMAC-mode-registered identity is rejected
end-to-end through the real HTTP pipeline in the same configuration every
other test in this suite already runs under (no fixture ever sets
`AGENT_ENVELOPE_HMAC_TEST_ONLY`). P1/P4's `quote_signing_key`/
`build_quote_token` HMAC-SHA256 usage is untouched — that is quote-token
signing, a separate mechanism from `AgentEnvelope` signing, and this
decision does not gate it.

### 7. `committed_total` nets reserved *and* settled ledger movements for the policy pre-check

`order.propose`'s pre-check (whether this purchase would exceed the
mandate's remaining budget) must count money already committed by prior
attempts against the same mandate — both amounts currently held in
`reserved` (an in-flight attempt not yet resolved) and amounts already
`spent` (a prior attempt that settled). `ledger_service.committed_total`
sums both via the same signed-direction netting ADR 0007 decision 1
established (`net_balance`), so a mandate that has already spent most of
its cap cannot be re-proposed past it even though no reservation for the
new attempt exists yet at pre-check time. This reuses P6's existing
netting primitive rather than inventing a second balance query.

### 8. `error` is §14's seventh message type, already implemented — the gap was in the first delivery's report, not the code

§14's table lists exactly seven rows, verbatim: `capability.discover`,
`catalog.query`, `quote.request`, `order.propose`, `order.status`,
`receipt.issue`, `error` — the last one directional "either," carrying
`reason_code`, a human message, and a `retryable` flag, with no response
of its own. `domain/agent/envelope.py`'s `MessageType` Literal already
included `"error"` as one of its seven values from the first draft of this
phase, and `merchant.build_response_envelope` already mapped every
`HandlerError` outcome (an unknown mandate on `quote.request`, an unknown
order on `order.status`/`receipt.issue`, an unsettled order on
`receipt.issue`, ...) to a response envelope with `type: "error"` — so the
seventh type was implemented and exercised (`test_all_seven_message_types_
end_to_end`'s step 6 already asserted `early_receipt_envelope["type"] ==
"error"`) before this correction pass.

What was missing was visibility: the first delivery's report named only
the six *request* types by name and never called `error` out as the
distinct seventh type, so a reviewer reading the report alone could not
confirm all seven were accounted for. Fixed by making the test itself the
source of truth for the count, not the report's prose:
`test_order_flow.py` now defines `_SEVEN_MESSAGE_TYPES` as the literal §14
list, asserts it equals `domain.agent.envelope.REQUEST_MESSAGE_TYPES |
{"error"}` (so the test's list can never silently drift from the wire
format's own), and the end-to-end test collects every response envelope's
`type` into a `seen_types` set across all seven steps, asserting at the
end that the set is exactly the seven-element §14 list — a single
assertion that fails loudly if any message type is ever missing, renamed,
or accidentally duplicated into fewer than seven distinct values.

## Consequences

- `POST /agent/v1/messages` is the only envelope-verified entry point;
  P4's plain-REST routes remain unauthenticated-by-envelope, matching
  ADR 0005 decision 11 — a future phase that wants envelope auth on those
  two routes directly, rather than via the agent-protocol message types,
  would need a separate decision.
- The NUL-byte pattern is duplicated (not shared) across
  `domain/agent/envelope.py`, `interfaces/agent/schemas.py`, and
  `interfaces/http/routers/catalog.py` — three one-line constants, not a
  shared import — because `domain` cannot depend on `interfaces` and a
  cross-layer shared constant for a one-line regex is not worth the
  coupling. Any future request-schema module handling attacker-controlled
  strings destined for SQL should add the same constant locally.
- Every business-layer rejection stays inside the closed `ReasonCode`
  registry and HTTP 200; only the seven fixed protocol-layer rejection
  reasons get distinct HTTP statuses. A future message type must not
  invent an eighth HTTP-status-bearing protocol rejection without
  updating `_REJECTION_STATUS` and this ADR.
- A second signing algorithm can never again be silently reachable in
  production: any future addition to `SUPPORTED_ALGORITHMS` needs its own
  explicit `Settings` gate plus a startup guard in the same shape as
  `_enforce_no_hmac_outside_pytest`, not just a domain-level "known
  algorithm" entry — the lesson decision 6 encodes is that "documented as
  a fallback" and "safe to accept" are not the same claim.
