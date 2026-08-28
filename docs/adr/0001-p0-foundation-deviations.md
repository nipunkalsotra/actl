# 0001 — P0 foundation deviations

Status: Accepted
Date: 2026-08-28

## Context

P0 ("Foundation & rails", §28) scaffolded the repository exactly per §6, §25,
§26, §27. Three points in the specification text didn't survive contact with
a running `import-linter`/`alembic` toolchain unchanged. Per §28's driving
instructions ("Any deviation from this specification gets written into
docs/adr/ as a numbered decision record, not left implicit in code"), they're
recorded here rather than silently absorbed.

## Decisions

### 1. `.importlinter` list syntax reformatted

§6.1 prints `source_modules = actl.domain` and `forbidden_modules =
actl.infrastructure, actl.interfaces, sqlalchemy, ...` as comma-separated
values on a single line — an artefact of the table cell being flattened
during PDF export. import-linter's actual `.ini` list format requires one
value per line; the comma-separated form is parsed as a single (nonexistent)
module name and fails immediately.

**Change:** reformatted every multi-value `source_modules` /
`forbidden_modules` field to one entry per line. Same module names, same
three contracts, same enforcement. No semantic change.

### 2. Contract 3's `actl.application.buyer_agent` repointed to `actl.application.agents`

§6.1 contract 3 names `actl.application.buyer_agent` as a source module that
must never import the Razorpay adapter. That module path never existed:
§25's repository tree puts buyer-agent, merchant-agent, envelope and signing
code together under `actl.application.agents` (`agents/{buyer,merchant,envelope,signing}.py`).
import-linter's `forbidden`-type contract requires source modules to exist
in the analyzed package, so the literal §6.1 path fails at lint time on the
real tree.

**Change:** contract 3's source module is `actl.application.agents` instead
of `actl.application.buyer_agent`. This is a superset of the original intent
— it also covers merchant-agent code, not just the buyer side — so the
guarantee (§P2: only the gate may reach the payment provider) is preserved
and, if anything, enforced more broadly, never narrowed.

### 3. First alembic revision is `0000`, not `0001`

§28 P0's exit criteria shows `curl -s localhost:8000/readyz` reporting
`"migration":"0001"`. But §28 P0's own deliverables list does **not**
include domain migrations — those are explicitly P2's: "Alembic migrations
for every table in §18.2" (§25 names that file `migrations/0001_core.sql`).
P0's instructions also say "Do not write any domain logic yet," and the
mandate/order/ledger/audit tables are domain schema.

**Change:** P0 ships one empty foundation revision, id `0000`, with no
domain tables — just enough for `make migrate` to run and `/readyz` to
report a real, current migration id. P2's `0001_core` chains onto `0000`
via `down_revision`, so there is no id collision when the real schema
lands. `/readyz` at P0 correctly reports `"migration":"0000"`, not the
illustrative `"0001"` from the spec text, because the domain schema that
`"0001"` refers to does not exist until P2.

## Consequences

- CI and local `make lint` / `make migrate` run against the real tree
  exactly as specified in intent, not as a literal transcription of a PDF
  table or an exit-criteria example that presupposes P2 has already run.
- P2 must set `down_revision = "0000"` on its first migration (`0001_core`)
  rather than `down_revision = None`.
- No mandatory security, payment, audit, idempotency, failure-handling, or
  test requirement from docs/architecture.md was simplified, omitted,
  combined, or weakened by any of the above — all three are naming/format
  corrections needed to make the specified contracts actually run.
