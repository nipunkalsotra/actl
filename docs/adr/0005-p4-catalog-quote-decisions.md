# 0005 — P4 catalog & quote decisions

Status: Accepted
Date: 2026-08-28

## Context

P4 ("Catalog, agent feed and price locks", §28) implements §13: the
agent-readable catalog, `POST /agent/v1/quote`, the well-known discovery
document, and a demo-only admin price mutation. §13 specifies the wire
shapes precisely but leaves several mechanics open — pagination style,
ETag construction, quote signing algorithm, how "the chain records"
covers a mutation §16.3's table predates. This ADR records those calls
plus one genuine testing-infrastructure gotcha found while building the
suite, alongside ADRs 0001–0004 for P0–P3.

## Decisions

### 1. `catalog_items.version` is a per-item last-changed watermark, not a copy of the global counter

§13.1's worked example shows both the feed's top-level `catalog_version`
and an item's own `version` as 118 — consistent with either "every item
repeats the global counter" or "each item stores the counter's value as of
its own last change." The latter is what's implemented: `version` updates
only when that specific item's price changes (via the admin mutation),
while items untouched by a given mutation keep their earlier value. This
is strictly more informative for a consuming agent (which items are
"fresher") and makes the stale-price scenario's assertion clean: after
mutating one SKU, only that SKU's `version` advances past the pinned
quote's `catalog_version`.

### 2. `catalog_meta` is a single always-present row, not a Postgres SEQUENCE

Same reasoning as ADR 0004 decision 4 (P3's `audit_log.seq`): a bare
`SEQUENCE`'s `nextval()` is non-transactional — it survives a rolled-back
transaction, so a failed-then-retried admin mutation would burn a version
number, forging a real-looking "the price changed" signal from nothing
happened. `catalog_meta` is a one-row table (`id='default'`) updated via
`UPDATE ... SET version = version + 1 RETURNING version` inside the same
transaction as the item write — both commit together or neither does.

### 3. `location`/`attributes`/`policy` are flattened SQL columns, not JSONB

§13.1 shows them as nested objects on the wire, but every one of them is
filterable or sortable in this build (`category`, `location`,
`max_unit_minor` are the doc's own example query params). Flattening keeps
`WHERE`/`ORDER BY` as plain indexed column comparisons instead of JSONB
containment operators, and the nesting is reconstructed at the domain-model
boundary (`application/catalog_service.py::_to_domain_item`) — the wire
shape is unaffected. `attributes`' three fields (`rating`, `sea_facing`,
`breakfast_included`) are the exact set §13.1's example shows; this build
has one category (`travel.hotel`), so a generic/extensible attribute bag is
not built ahead of a second category actually existing.

### 4. Keyset pagination on `(unit_price_minor, sku)`, not OFFSET

Instruction 2's "stable ordering explicit so paging cannot duplicate or
skip records" is a correctness requirement, not just a style note: OFFSET
pagination silently duplicates or skips rows when the underlying set
changes between page requests (an admin mutation between two page fetches
would do exactly that). Keyset pagination — `next_cursor` encodes the last
row's `(unit_price_minor, sku)`, the next page's `WHERE` clause is a row-
value comparison `> (price, sku)` — is immune to that class of bug by
construction. Default ordering is cheapest-first; this is a stable tie-
break, not a ranking algorithm (ranking is P8's explicit deliverable).

### 5. Strong ETag = `cat-v{catalog_version}-{4-hex filter hash}`

§13.1's example ETag (`"cat-v118-a91f"`) is illustrative; the format
implemented reuses that shape but the 4-hex suffix is a truncated
`sha256(jcs(filters))`, not decoration — without it, two different
filtered views taken at the same `catalog_version` would collide on one
ETag, and a client's `If-None-Match` from one view could wrongly 304 a
request for a different one. Uses `domain.audit.canonical.jcs` (P1),
not a new canonicaliser.

### 6. `quote_token` is HMAC-SHA256, signed via P1's existing primitives — not Ed25519

§14.1 documents HMAC-SHA256 as agent-protocol's development fallback, and
this is a 100%-test-mode build (`config.py`'s own docstring). Ed25519
requires an agent-identity/key registry that doesn't exist until P7.
`domain/catalog/quote.py` calls `mandate.signing.sign_spec_hash` and
`verify_signature` directly — the same HMAC-SHA256 functions P1 built for
`spec_hash`, applied here to `quote_hash` — rather than a second signing
implementation (§28 P4 instruction 3's explicit requirement). The
well-known document advertises exactly this — `"algorithms":
["HMAC-SHA256"]`, not `ed25519` — since advertising an algorithm nothing
implements yet would make the discovery document itself misleading.

### 7. `AuditAction.CATALOG_PRICE_MUTATED` extends §16.3's registry

§16.3's event table predates P4's admin endpoint in §28 and has no action
for "an admin changed a price." Instruction 5's "must never bypass audit"
requires one to exist. Rather than overload `CATALOG_QUERIED` (a read) or
leave the mutation unaudited, the closed `AuditAction` enum (ADR 0004's
closed-registry discipline, unchanged) gains one new member. `mutate_price_
demo_only` writes it through the same `application.audit_service.
append_entry` every other write in the system uses — same chain, same
guarantees, no bypass.

### 8. Admin auth: a single shared bearer token, `hmac.compare_digest`-compared

Appendix A specifies "Auth: Admin token" without a mechanism. No API-key or
session system exists yet in this build, so the minimal, correct
implementation is one `settings.admin_token` (a demo placeholder, same
spirit as the Razorpay test key), checked as `Authorization: Bearer
<token>` via a timing-safe comparison. A missing or wrong token is 401. A
real admin-identity system is out of scope for a demo-only endpoint that
§28 explicitly says exists "only to trigger the stale-price scenario."

### 9. P4 makes drift *detectable*; P6's Money Action Gate makes it *enforced*

§13.2 point 3 ("Gate G5 re-checks expiry and catalog version at execution
time... STALE_PRICE") and the mandate-budget/status checks a real
`quote.request` would need are gate logic — P6's explicit deliverable
("seven gates"), not P4's. `create_quote` here validates only that the
referenced mandate and sku exist and the sku is in stock (§28 P4's own
scope: "do not perform payment, order creation, capture"). The stale-price
test proves the *precondition* G5 will later check: after an admin
mutation, a previously-issued quote's pinned `catalog_version`/
`unit_price_minor` provably diverge from the live feed's — not that a
STALE_PRICE verdict is produced, which requires machinery this phase does
not build.

### 10. `catalog.queried` is written only when the feed is actually delivered, not on a 304

§16.3 lists `catalog.queried` as "Agent reads the feed." A conditional
`If-None-Match` request that resolves to 304 delivers no feed content —
the ETag pre-check (`uow.catalog.current_version()`) happens before the
audit write, and a match short-circuits before `list_catalog()` (and its
audit entry) ever runs. A full 200 response always writes the entry.

### 11. `GET /agent/v1/catalog` and `POST /agent/v1/quote` are plain typed REST, not envelope-wrapped

Appendix A's auth column says "Signed envelope" for these routes — that's
§14's agent-to-agent protocol layer, which is P7's explicit deliverable
("signed envelopes, buyer ↔ merchant handshake"), built *on top of* what
P4 exposes, not the reverse (P7 depends on P4 in the phase roadmap, §28
Figure 28.1). §28 P4's own deliverables list and CLAUDE CODE PROMPT do not
ask for envelope verification here. Building it now would mean
implementing agent-identity/key registration (P7 scope) inside a phase
explicitly scoped to catalog and quotes.

### 12. TestClient's background event loop needs its own, unbound-until-first-use engine

Not a spec decision — a real bug hit while writing the test suite.
Starlette's `TestClient` runs the ASGI app on a background thread with its
own event loop, separate from pytest-asyncio's session-scoped loop that
this repo's shared `engine`/`session_factory` fixtures (ADR 0004's P3
testcontainer setup) are bound to. Reusing those fixtures directly inside
an HTTP dependency override crosses that boundary — asyncpg raises "Future
attached to a different loop." Fixed by giving `tests/integration/catalog`'s
`client` fixture its own `create_async_engine(postgres_url)` — engine
construction does no I/O and binds to no loop, so building one in a sync
fixture body and only ever touching it from inside `TestClient`'s requests
(or via `TestClient.portal.call(...)` for test-side seeding) keeps
everything on one loop. Reuses the same session-scoped Postgres container
(`postgres_url`), so no second container spins up.

## Consequences

- P6, when it builds gate G5, reads exactly the fields P4 already exposes
  (`quote.catalog_version` vs `uow.catalog.current_version()`) — no schema
  change needed, only the enforcement logic.
- P7's envelope layer wraps `GET /agent/v1/catalog`/`POST /agent/v1/quote`
  without changing their bodies; Ed25519 signing replaces HMAC-SHA256 for
  `quote_token` once P7's agent-identity registry exists — `domain/catalog/
  quote.py`'s `build_quote_token`/`parse_and_verify_quote_token` isolate
  that change to one module.
- Any future phase adding a second catalog category should revisit
  decision 3 (flattened attribute columns) before adding a third — two
  fixed shapes can stay as columns; three heterogeneous ones likely want a
  typed-but-JSONB attributes column instead.
