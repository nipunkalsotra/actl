# 0004 — P3 trust layer decisions

Status: Accepted
Date: 2026-08-28

## Context

P3 ("Trust layer — hash chain, Merkle, verifier", §28) implements §16's
append-only hash chain, Merkle checkpoints, `actl verify-chain`, the offline
export bundle, and the Anchor port. §16 gives the chain formula exactly
(`entry_hash = sha256(prev_hash_bytes || sha256(jcs(payload)))`, genesis =
32 zero bytes) but leaves several mechanics unspecified at the byte level,
and building this phase surfaced two real correctness bugs — one in a P2
test fixture, one in checkpoint-boundary handling — that are recorded here
alongside the interpretive decisions. None of the below weakens a mandatory
security, audit, or test requirement; each is either a necessary
completion of an underspecified area, a documented inherent limitation, or
a genuine bug found and fixed during implementation.

## Decisions

### 1. Merkle tree construction: RFC 6962 domain-separated binary tree

§16.1 says "a root is computed and stored" every `AUDIT_CHECKPOINT_EVERY`
entries but gives no leaf-hash, node-hash, or odd-node-handling formula.
`domain/audit/merkle.py` uses the Certificate Transparency (RFC 6962)
construction: `leaf_hash(h) = sha256(0x00 || h)`, `node_hash(l, r) =
sha256(0x01 || l || r)`, and a lone trailing node at any level is promoted
unchanged rather than duplicated. Domain separation closes the classic
Merkle ambiguity where an internal node's hash could otherwise be presented
as a valid leaf; promotion-not-duplication avoids the class of bug behind
Bitcoin's CVE-2012-2459. `AUDIT_CHECKPOINT_EVERY` (64) is a power of two,
so the odd-node path never actually triggers in normal operation — it's
implemented anyway because "never simplify away… security measures"
applies to code paths as much as to features.

### 2. Checkpoints cover independent segments, not a cumulative range from seq 1

§18.2's `audit_checkpoints` table has both `from_seq` and `to_seq` columns,
and §16.1 says "verifying a recent segment does not require rehashing the
entire history" — read together, each checkpoint's Merkle root is computed
only over its own segment's entries (e.g. checkpoint 2 covers 65..128, not
1..128). A cumulative-from-genesis tree would make that "recent segment"
claim false for any segment past the first.

### 3. Checkpointing is synchronous, inside the triggering append's own transaction — not a separate worker

§25's compressed file list mentions a "checkpoint worker"; requirement 4
here asks for the checkpoint to be "persist[ed]… atomically with the
relevant audit state." A worker polling for uncounted checkpoints would
need its own, separate transaction — a window would exist where entry 64
is committed but its checkpoint isn't. `_write_checkpoint()` runs inside
`append_entry()`'s own transaction instead: when seq crosses a boundary,
the checkpoint is computed and inserted before that same transaction
commits, making atomicity trivially true rather than something a second
process has to get right independently.

### 4. `seq` is assigned explicitly (`MAX(seq)+1` under the advisory lock), never the column's BIGSERIAL default — and this surfaced a real bug

§16.1: "seq is assigned inside the same transaction as the append; a gap is
itself detectable evidence of tampering." A `BIGSERIAL` sequence advances
on `nextval()` even when its transaction rolls back — Postgres sequences
are deliberately non-transactional — so relying on the column default would
let a failed-then-retried append silently skip a number on every retry,
manufacturing exactly the "gap = tampering evidence" signal on ordinary
operational hiccups. `add_at_seq()` (the locked, concurrency-safe append
path) and `add()` (the plain, single-writer path P2's
`test_commit_writes_state_audit_and_event` uses) both now compute seq
explicitly via `SELECT COALESCE(MAX(seq), 0) + 1`.

**Bug this caught**: mixing the two insertion styles in the same shared
`audit_log` table breaks it — an explicit-seq insert never advances the
underlying sequence object, so a later insert relying on the BIGSERIAL
default can collide with an already-taken seq. This actually happened
while wiring up the P3 test suite (`UniqueViolationError: duplicate key
value violates unique constraint "audit_log_pkey"`) once P2's
`test_uow_atomicity.py` and this phase's tests shared a session against the
same table. Fixed by removing every reliance on the BIGSERIAL default from
the codebase, not by reordering tests to hide it.

### 5. P2 test fixtures needed well-formed `sha256:<64-hex>` values, not arbitrary strings

`test_uow_atomicity.py` and `test_append_only_triggers.py` (P2) inserted
`audit_log` rows with placeholder values like `prev_hash="sha256:0"` or
`entry_hash=f"sha256:{new_id('trg')}"` — harmless while nothing read them
as real hashes. P3's chain reader (`get_tail()` → `parse_hex_prefixed()`)
now scans the same shared table for the current tail and treats every row
as a genuine link, so a malformed placeholder crashes the next append with
`ValueError: non-hexadecimal number found in fromhex()`. Fixed by
generating real `hashlib.sha256(...).hexdigest()` values in both fixtures —
same test assertions, same coverage, just well-formed data. This is a
compatibility fix, not a change to what either P2 test verifies.

### 6. "First divergence: payload.amount_minor" is not implemented

§16.2's illustrative tamper-demo output includes a field-level diagnostic
line. A hash mismatch alone doesn't reveal *which* field changed — SHA-256
is one-way, and the verifier has no second copy of the untampered payload
to diff against. Requirement 5 here asks for "the exact divergent seq,
expected value, and actual value," which `ChainBreak` provides in full;
the field-level line is treated as demo narration rather than a literal,
mechanically-derivable feature.

### 7. Standalone bundle verification is fully trustless only when exported from seq 1

`verify_bundle.py` needs a starting `prev_hash` to walk the chain forward.
For `from_seq == 1` that's the well-known, unfalsifiable genesis constant —
full offline trust, no assumptions. For `from_seq > 1` (a partial export),
nothing in the bundle independently proves what preceded it; the verifier
trusts the first exported entry's own claimed `prev_hash` and says so
explicitly (`NOTE: partial range … chain start trusted from the bundle's
own claim`). This is an inherent limitation of offline verification without
an external anchor (§16.1's "not yet trustless… anchoring is what closes
that gap" RISK/GUARD applies here too), not a shortcut — closing it for
partial exports would require the very anchoring P3 explicitly excludes.
Payload tampering on the first entry itself is still caught regardless
(the entry_hash-vs-payload check doesn't depend on where prev_hash came
from); only a *consistently re-forged* first entry could evade it, which
is exactly the residual gap the RISK/GUARD already names.

### 8. Checkpoint verification only considers checkpoints whose *entire* segment lies inside the verified range — a real bug found and fixed in both verifiers

A checkpoint's segment can straddle the start of a partial `--from`/`--to`
range (e.g. segment 7..9 when verifying from seq 8). The initial
implementation checked only `checkpoint.to_seq` against the range, so it
would try to recompute a Merkle root from a truncated, wrong set of entries
and falsely report `CHAIN BROKEN` — this surfaced while writing the export
bundle's own isolation test and reproduces identically in `verify_chain()`
(the CLI's live path) once traced back. Fixed in both `audit_service.
verify_chain()` and the embedded `verify_bundle.py` template by requiring
`segment_start_seq >= from_seq` before attempting a checkpoint's root
check; `export_bundle()`'s checkpoint filter was tightened to match
(`from_seq <= c.from_seq and c.to_seq <= to_seq`, not just the endpoint).
`test_verify_chain_skips_checkpoints_whose_segment_is_not_fully_in_range`
pins this.

### 9. `application/audit_service.py` depends only on the `Anchor` protocol — never imports `NoopAnchor`

Per instruction 7's explicit phrasing ("application depends on the port,
infrastructure supplies NoopAnchor"), `append_entry()`'s `anchor` parameter
defaults to `None` rather than to a concrete `NoopAnchor()` instance —
omitting it produces the same no-anchoring behaviour a no-op adapter would,
without `actl.application` ever importing `actl.infrastructure.anchor`.
Callers who want that choice visible construct `NoopAnchor()` themselves
(as the tests do). This keeps the dependency direction genuinely inward
even though import-linter's current contracts don't mechanically forbid an
application→infrastructure import in general (per ADR 0003 decision 8) —
here the task asked for the port pattern specifically, so it's applied
specifically.

### 10. `actl verify-chain` (CLI) calls the same `audit_service.verify_chain()` the tests exercise

No separate verification logic in `cli.py` — the CLI is a thin
argparse-to-`asyncio.run()` wrapper printing `ChainVerificationResult`. The
only independent reimplementation of the chain/Merkle logic is the
deliberately-standalone `verify_bundle.py` template (instruction 6 requires
it to run with zero `actl` imports), and that one is generated by copying
`domain/audit/{canonical,chain,merkle}.py`'s actual source verbatim at
export time — the same implementation, not a second one.

## Consequences

- P9 (failure theatre) and the demo script should use `from_seq=1` when
  showing the export bundle to a judge, per decision 7 — that's the only
  fully trustless offline verification story this phase provides.
- P4+ phases writing their own `audit_log` rows (catalog queries, quotes,
  orders) must go through `application.audit_service.append_entry()`, not
  raw inserts — decision 4's gaplessness guarantee only holds for callers
  that do.
- Real anchoring (Monad testnet or otherwise), if it ever lands, is a new
  `infrastructure/anchor/monad_testnet.py` implementing the existing
  `Anchor` protocol — no change to `application/audit_service.py` or
  `application/ports.py` required, per decision 9.
