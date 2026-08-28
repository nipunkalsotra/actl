# actl — Agentic Commerce Trust Layer

A merchant an autonomous AI buyer can transact with end to end, where every
money action is explainable, bounded, gated and audited. Full design in
[`docs/architecture.md`](docs/architecture.md).

## 90 seconds

```
make up && make migrate && make demo
```

`make up` brings up Postgres and Redis, `make migrate` applies the schema,
`make demo` runs the scripted scenarios end to end and prints the audit
chain head. `make lint` and `make test` run the architectural contracts and
test suite in CI (see `.github/workflows/ci.yml`).

## Status

**P0 — Foundation & rails.** Repo scaffold, config with the test-mode guard
(§21.4), the platform primitives (clock, ids, logging, errors, retry,
breaker), and the `.importlinter` architectural contracts (§6.1) are in
place. No domain logic yet — that's P1.

See `docs/architecture.md` §28 for the full phase roadmap.
