# actl — Agentic Commerce Trust Layer

A merchant an autonomous AI buyer can transact with end to end, where every
money action is explainable, bounded, gated and audited. Full design in
[`docs/architecture.md`](docs/architecture.md).

## 90 seconds

```
make up && make migrate && ./scripts/demo.sh
```

`make up` brings up Postgres and Redis, `make migrate` applies the schema,
`scripts/demo.sh` runs the six §20.1 demo commands end to end — the five
named fault scenarios plus a closing chain verification — and prints each
one's detected fault, terminal outcome, recovery/compensation action,
reserved balance and audit-chain status.

`make demo` re-runs the same five scenarios against a disposable, isolated
database and validates their traces byte-for-byte against the committed
golden fixtures (`fixtures/golden_traces/`) — the automated, CI-safe
counterpart, no `make up` required. `make chaos` runs the full F1–F10
fault-injection suite (`tests/chaos/`) the same way. `make verify` checks
the live audit chain and re-verifies the golden fixtures offline. `make
lint` and `make test` run the architectural contracts and unit/property
test suite in CI (see `.github/workflows/ci.yml`).

Operators: see [`docs/runbook.md`](docs/runbook.md) for the detection,
containment, recovery and escalation steps for each of the ten failure
modes.

## Status

**P0–P9 complete.** Every phase in the roadmap below is implemented and
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

See `docs/architecture.md` §28 for the full phase roadmap and exit criteria.
