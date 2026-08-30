# 0013 — Append-only hash chain over a blockchain/DLT

Status: Accepted
Date: 2026-08-30

## Context

§30's "Audited" bar requires a tamper-evident record a third party can
independently verify — not merely trust because actl says so. The
buildathon's own domain (agentic commerce, trust, provenance) is exactly
the kind of problem statement that invites reaching for a blockchain or
some other distributed-ledger technology. The architecture has to decide
what actually backs the audit trail's tamper-evidence claim.

## Decision

The audit trail is a single-writer, append-only Postgres table
(`audit_log`, §18.2) where every row's `entry_hash` is a SHA-256 digest
over the canonicalised payload plus the previous row's `entry_hash`
(§16.1) — a hash chain, not a blockchain. Every `AUDIT_CHECKPOINT_EVERY`
rows, a Merkle root over that segment is computed and persisted
atomically, in the same transaction as the entry that crosses the
boundary (§16.1's own "WHY THIS WAY": no second process has to get
"persist checkpoint metadata atomically with the relevant audit state"
right). A Postgres advisory lock keyed by chain id (`acquire_chain_lock`,
`application/audit_service.py`) serialises concurrent appends so two
writers can never read the same `prev_hash` and fork the chain. An
optional `Anchor` port (§16.1's stretch goal) can publish just the
Merkle root to any external anchor — a no-op adapter is the default, so
"trustworthy" never depends on a paid service being reachable.

## Consequences

- **Verification needs nothing but the standard library.** `make
  bundle`'s exported bundle (NDJSON evidence, checkpoint/Merkle roots, a
  manifest, a standalone verifier with the canonicalisation/hashing logic
  copied verbatim) verifies from a completely isolated directory — no
  database, no network, no `actl` package installed, no consensus
  protocol, no gas fee (`tests/integration/audit/test_export_bundle.py`
  proves this in a subprocess with `actl` deliberately unimportable).
- **Tamper detection is exact and immediate.** `actl verify-chain`
  reports the precise broken sequence number the moment a single byte of
  a single payload changes (§30: "the tamper test reports the exact
  broken sequence number") — a property a hash chain gives for free and
  a blockchain gives no more of, at none of the operational cost.
- **A single Postgres instance is a single point of failure for new
  writes**, unlike a distributed ledger's multi-node replication — an
  accepted trade-off given the free-tier target runtime (§00 SCOPE) and
  because the optional anchor port exists precisely to let a future
  deployment publish the root externally without redesigning the chain
  itself.

## Alternatives considered

- **A real blockchain/DLT** (a permissioned chain, or anchoring every
  entry to a public chain). Rejected: consensus, gas fees, and node
  operation solve a problem actl doesn't have — a single merchant's own
  audit trail needs tamper-evidence and independent verifiability, not
  distributed agreement among untrusting parties who all write to the
  same ledger. §16.1's own anchor port keeps the *option* to publish a
  root externally open without paying that cost for every entry.
- **Digital signatures per entry instead of hash chaining.** Rejected as
  the sole mechanism: a signature proves *who* wrote an entry, not that
  entry N wasn't quietly removed or reordered relative to N+1 — the
  chain's `prev_hash` linkage is what makes reordering or deletion
  detectable, which is the property a tamper test actually needs to
  demonstrate.

## Relevant architecture section

§16 The trust layer — append-only audit chain; §16.1 Hash chain and
Merkle checkpoints; §18.2 `audit_log` schema.
