# actl agent-to-agent protocol (`actl.acp/1`)

A concise, reviewer-facing map of the wire protocol. **[`docs/architecture.md`](architecture.md) is authoritative** — §8.4 (AgentEnvelope), §13 (catalog/quote), §14 (the seven message types), and Appendix A (full HTTP API surface) are the full specification; this page is a navigable companion, not a second source of truth. The machine-readable JSON Schemas for every request/response body are committed under [`docs/protocol/`](protocol/) and linked inline below.

## Protocol version

Every `AgentEnvelope` carries `"protocol": "actl.acp/1"` — a literal constant, checked first, before any other verification step (`domain/agent/envelope.py::is_known_protocol_version`). An envelope with any other value is rejected `400 UNKNOWN_PROTOCOL_VERSION` before signature verification is even attempted.

## Two transport shapes

| Surface | Auth | Schemas |
|---|---|---|
| `POST /agent/v1/messages` | Signed `AgentEnvelope` (Ed25519) | one dispatch endpoint for all seven message types below |
| `GET /agent/v1/catalog`, `POST /agent/v1/quote` | None — plain typed REST | [`catalog.schema.json`](protocol/catalog.schema.json), [`quote.schema.json`](protocol/quote.schema.json) |

The catalog/quote routes are a **documented, deliberate deviation** from Appendix A's "Signed envelope" auth column: `src/actl/interfaces/http/routers/catalog.py`'s own docstring and [ADR 0005](adr/0005-p4-catalog-quote-decisions.md) record that P4's own deliverables scope never asked for envelope verification on these two reads/quotes — they were built as plain REST and never retrofitted once P7 added the signed envelope layer for the other five message types. Nothing about these two routes moves money or binds a mandate, so this is a scope decision, not a security gap.

## AgentEnvelope

Schema: [`agent-envelope.schema.json`](protocol/agent-envelope.schema.json).

```json
{
  "protocol": "actl.acp/1",
  "msg_id": "msg_...",
  "ts": "2026-08-30T12:00:00Z",
  "from": "agt_buyer_01",
  "to": "agt_merchant_01",
  "corr_id": "01JX8Z7C1M4RQ",
  "type": "order.propose",
  "body": { "...": "type-specific, see the seven message types below" },
  "sig": { "alg": "Ed25519", "key_id": "ed25519:...", "value": "..." }
}
```

`sig` covers the canonicalised envelope (§8.4's JCS canonical-JSON form) with the `sig` field itself excluded — the signature can never sign over its own value. `corr_id` is architecturally the same identifier as the OpenTelemetry trace id and the audit chain's own `trace_id` column (§22: "corr_id equals the OpenTelemetry trace id and is written into every audit entry"), so one value threads a client's own correlation id all the way through the causal chain `GET /audit/explain/{order_id}` returns.

## Verification pipeline (`application/agents/envelope_service.py::verify_envelope`)

In order, first failure wins — every step short-circuits before anything more expensive (a DB read, a nonce claim) runs:

1. **Protocol version** — `protocol == "actl.acp/1"`, else `400 UNKNOWN_PROTOCOL_VERSION`.
2. **Algorithm** — `sig.alg` is `Ed25519`, or `HMAC-SHA256` *only* when `AGENT_ENVELOPE_HMAC_TEST_ONLY` is set — a setting `config.py` itself refuses to honor outside pytest (a startup check kills the process if it's set anywhere else). Anything else: `400 UNKNOWN_ALGORITHM`.
3. **Identity resolution** — `sig.key_id` must resolve to a registered `agent_identities` row *whose own `agent_id` matches the envelope's claimed `from`* — a key valid for one agent can never authenticate a message claiming to be from a different agent. Unknown key_id or mismatch: `401 IDENTITY_UNKNOWN`. Revoked: `403 IDENTITY_REVOKED`. Outside its `not_before`/`expires_at` window: `403 IDENTITY_EXPIRED`.
4. **Ed25519 verification** — the resolved identity's own public key verifies the envelope's signature over the canonical bytes. Failure: `401 SIGNATURE_INVALID`.
5. **Replay protection** — `nonce_cache.claim(msg_id)` (Redis `SET NX EX`, real atomicity, never a fake). A losing claim (already seen): `409 REPLAYED_MESSAGE`. An unreachable cache fails **closed** — never treated as "first delivery" — `503 REPLAY_CHECK_UNAVAILABLE`, `retryable: true`.
6. **Timestamp skew** — `|now - ts| <= 120s` (`SKEW_WINDOW_S`), checked last, against the injected `Clock` only (never wall-clock `datetime.now()` directly). Outside the window: `400 CLOCK_SKEW`.

A rejection at any of these six steps is a **protocol-layer** rejection: plain, unsigned JSON (`{"reason_code", "message", "retryable"}`, schema [`message-error.schema.json`](protocol/message-error.schema.json)), a non-200 HTTP status per the table below — there is no verified identity yet to address a signed response to. Business-layer outcomes (reached only once `verify_envelope` succeeds) are different: always **HTTP 200**, always a full, Ed25519-signed response envelope — a typed "error" body inside a *signed* envelope is itself a legitimate, well-formed protocol response (§14's own message table), never conflated with a protocol-layer bounce.

| reason_code | HTTP status |
|---|---|
| `MALFORMED_REQUEST`, `UNKNOWN_PROTOCOL_VERSION`, `UNKNOWN_ALGORITHM`, `CLOCK_SKEW` | 400 |
| `IDENTITY_UNKNOWN`, `SIGNATURE_INVALID` | 401 |
| `IDENTITY_REVOKED`, `IDENTITY_EXPIRED` | 403 |
| `REPLAYED_MESSAGE` | 409 |
| `REPLAY_CHECK_UNAVAILABLE` | 503 (`retryable: true`) |

## The seven message types

Dispatched by `AgentEnvelope.type` at `POST /agent/v1/messages` (`interfaces/agent/routes.py::_dispatch`); each request/response body schema below is the `body` field's shape, never the envelope itself.

| type | request schema | response schema | §14 summary |
|---|---|---|---|
| `capability.discover` | [`capability_discover_request`](protocol/capability_discover_request.schema.json) | reuses [`agent-commerce.schema.json`](protocol/agent-commerce.schema.json) | supported protocol versions; response is the same document `GET /.well-known/agent-commerce.json` serves |
| `catalog.query` | [`catalog_query_request`](protocol/catalog_query_request.schema.json) | reuses [`catalog.schema.json`](protocol/catalog.schema.json) | structured filters only, never free-text prose |
| `quote.request` | [`quote_request`](protocol/quote_request.schema.json) | [`quote`](protocol/quote.schema.json) | pins price + `catalog_version` at this instant, signs a `quote_token` |
| `order.propose` | [`order_propose_request`](protocol/order_propose_request.schema.json) | [`order_propose_response`](protocol/order_propose_response.schema.json) | the money-authorizing message — see the trust boundary below |
| `order.status` | [`order_status_request`](protocol/order_status_request.schema.json) | [`order_status_response`](protocol/order_status_response.schema.json) | order/payment state + audit sequence range |
| `receipt.issue` | [`receipt_issue_request`](protocol/receipt_issue_request.schema.json) | [`receipt_issue_response`](protocol/receipt_issue_response.schema.json) | only once `status == "CAPTURED"`, else `ORDER_NOT_SETTLED` (retryable) |
| `error` | — response-only | [`message-error.schema.json`](protocol/message-error.schema.json) | never a request type; constructed by the merchant from a `HandlerError` |

## The merchant mandate-hash trust boundary (`order.propose`)

`order_propose_request`'s schema carries **only ids and hashes** — `quote_id`, `quote_hash`, `mandate_id`, `mandate_spec_hash`, `intent_hash` — never a mandate body. This is a deliberate, load-bearing design choice (§8.4's own "WHY THIS WAY" note), implemented in `application/agents/merchant.py::handle_order_propose`:

1. The merchant loads its **own, previously-confirmed** mandate from its own database by `mandate_id` — it never parses, persists, or trusts a buyer-supplied mandate body, because the wire protocol has no such field to supply in the first place.
2. The buyer's claimed `mandate_spec_hash` is compared against the merchant's own stored `mandate.spec_hash` — a mismatch is `MANDATE_TAMPERED`, rejected before the policy engine or the Money Action Gate ever runs.
3. The buyer's claimed `quote_hash` is compared the same way against the merchant's own stored quote.
4. The merchant independently *recomputes* `intent_hash` from its own quote + mandate + catalog data (`compute_intent_hash`) and compares it against the buyer's claim — `INTENT_MISMATCH` on any divergence.

A compromised or malicious buyer-agent cannot smuggle in a modified mandate with a wider spending cap: there is no field to put one in, and every hash the buyer *does* supply is only ever compared against the merchant's own records, never trusted as data. §28 P7 instruction 5's own negative test (`tests/contract/test_agent_protocol_schemas.py` and the P7 gate tests) proves an altered mandate/intent body is inert.

## Well-known discovery document

`GET /.well-known/agent-commerce.json` (schema: [`agent-commerce.schema.json`](protocol/agent-commerce.schema.json)) — no auth, the intended bootstrap path for an agent with no prior out-of-band configuration:

```json
{
  "protocol": "actl.acp/1",
  "currency": "INR",
  "endpoints": {
    "catalog": "/agent/v1/catalog",
    "quote": "/agent/v1/quote",
    "messages": "/agent/v1/messages"
  },
  "signing": { "algorithms": ["Ed25519", "HMAC-SHA256"] },
  "limits": { "quote_ttl_s": 120 }
}
```

`capability.discover` (message type, over `/agent/v1/messages`) returns this exact same document — one source, two access paths.

## Verifying this document stays honest

`tests/unit/test_protocol_docs.py` asserts every schema path referenced above exists on disk and is valid JSON — a broken or renamed link here fails CI, not just a reviewer's click.
