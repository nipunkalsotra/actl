# 0016 — P11 Monad Testnet anchoring decisions

Status: Accepted
Date: 2026-08-31

## Context

§16.1 names Monad Testnet anchoring of Merkle checkpoint roots as an
explicit stretch goal, kept out of scope through P0–P10: "the root — and
only the root — may be written to a Monad testnet transaction... this is
a stretch goal: the local chain already provides tamper-evidence, and
anchoring adds external timestamping." P11 implements it, as a strictly
bounded optional enhancement, without weakening any P0–P10 guarantee.

ADR [`0004`](0004-p3-trust-layer-decisions.md) decision 9 defined the
`Anchor` port (`application/ports.py`) and predicted: "Real anchoring
(Monad testnet or otherwise), if it ever lands, is a new
`infrastructure/anchor/monad_testnet.py` implementing the existing
`Anchor` protocol — no change to `application/audit_service.py` or
`application/ports.py` required." That prediction assumed real anchoring
would be wired into `_write_checkpoint`'s existing synchronous call to
`anchor.anchor_root(merkle_root)`. Building P11 for real surfaced why that
assumption doesn't hold, and this ADR records the resulting design.

## Decisions

### 1. Real anchoring is never wired into `application/audit_service.py`'s synchronous checkpoint path

`_write_checkpoint` calls `anchor.anchor_root(root_hex)` inside the same
Postgres transaction (and advisory lock) as the audit entry that crossed
the checkpoint boundary. If a real `MonadAnchor` were passed there, a slow
or unreachable Monad RPC call would hold that lock and transaction open —
directly contradicting the non-negotiable rule that "no payment, ledger,
gate, saga, checkout, or audit append action may wait on or fail because
Monad is unavailable." Real anchoring therefore never runs from that call
site: every real caller of `append_entry()` (12 call sites across
`catalog_service`, `payment_service`, `gate`, `orchestrator.saga`,
`compensations`, `agents.merchant`, `ledger_service`) continues to omit
`anchor=` exactly as it did before P11 — byte-for-byte the same behaviour
ADR 0004 established.

### 2. Real anchoring is a new, independent worker loop, not a port implementation wired into the append path

`actl.worker` gains a third loop, `_anchor_loop`, started only when
`ANCHOR_PROVIDER=monad` (never for the default `noop`). It polls
`audit_checkpoints` for `anchor_status='unanchored'` rows — that table
itself is the outbox called for in instruction 4, rather than a second,
new table — and submits each one using the platform's existing retry
(`platform/retry.py`) and circuit-breaker (`platform/breaker.py`)
primitives, the same ones already protecting Razorpay calls. This
mirrors `worker.py`'s existing `_webhook_loop`/`_reconcile_loop` shape
(§28 P5) rather than introducing new infrastructure.

### 3. `MonadAnchor.anchor_root()` exists, but deliberately raises `NotImplementedError`

The task instruction ("implement MonadAnchor as the infrastructure
implementation of the existing Anchor port") is honoured literally:
`MonadAnchor` satisfies the `Anchor` protocol's structural shape
(`async def anchor_root(self, merkle_root: str) -> str | None`), and a
unit test proves `anchor: Anchor = MonadAnchor(...)` type-checks and
assigns cleanly. But the on-chain contract requires `(auditChainId,
startSeq, endSeq, merkleRoot)` — a bare root string is not enough
information to submit a real anchor safely or meaningfully. Rather than
have `anchor_root()` silently do nothing (misleading — it would look like
a real integration point that quietly never fires) or guess at a range,
it raises `NotImplementedError` with a message pointing at the real entry
point, `anchor_checkpoint()`. This method is never called from any real
code path in this build (confirmed by
`tests/architecture/test_boundaries.py::test_only_worker_or_anchor_factory_imports_the_monad_adapter`
and `test_application_layer_never_imports_the_monad_adapter_or_web3`).

### 4. Why decision 1-3 is not actually a violation of ADR 0004's "no change to ports.py" prediction — and why it doesn't matter that it's close

`application/ports.py` and `application/audit_service.py` are, in fact,
completely unmodified by P11 (verified: `git diff` touches neither file).
ADR 0004's letter is satisfied. Its *reasoning* — that the `Anchor`
port's own `anchor_root` method would be the real integration point — is
superseded by this ADR: the real integration point is
`infrastructure/anchor/monad_testnet.py::MonadAnchor.anchor_checkpoint()`,
called only from `actl.worker`, never through the `Anchor` protocol at
all. This ADR supersedes that specific expectation in ADR 0004 decision 9
while leaving every other decision in that ADR (Merkle construction,
advisory-lock serialisation, synchronous checkpoint writes,
`NoopAnchor`-as-default) fully intact.

### 5. Testnet-only is enforced mechanically, not just documented

`infrastructure/anchor/factory.py::build_anchor_worker` raises
`SystemExit` at construction if `MONAD_CHAIN_ID` is ever anything other
than `10143` (Monad Testnet's confirmed chain id, verified against
official docs — see `docs/monad-testnet.md`). There is no mainnet RPC
default and no configuration path that produces a mainnet transaction.

### 6. `audit_checkpoints` gains anchor-state columns; `audit_log` is never touched

Per instruction 4 ("persist anchor status and transaction metadata only
in the appropriate checkpoint/anchor persistence model — do not mutate
immutable `audit_log` rows"), migration `0009` adds `anchor_status`,
`anchor_chain_id`, `anchor_contract_address`, `anchor_attempts`, and
`anchor_last_error` to `audit_checkpoints` (a table that already carried
unused `anchor_tx`/`anchored_at` columns from P3, now finally populated).
`audit_log`'s append-only trigger (§18.2, migration `0002`) is completely
unaffected — no new write path to that table exists.

### 7. Idempotency is enforced twice: once on-chain, once in the retry layer

`AuditCheckpointAnchor.sol::anchor()` treats an identical resubmission
(same `auditChainId`/`startSeq`/`endSeq`/`merkleRoot`) as a no-op success,
and rejects a *different* root for an already-anchored range with a
`ConflictingRoot` revert. `MonadAnchor.anchor_checkpoint()` additionally
reads `getCheckpoint()` before writing, so a retried delivery from the
worker's own retry loop never even submits a redundant transaction (saves
gas, and makes the happy path's "idempotent" claim true at the
application layer too, not just the contract layer).

### 8. Deliberately broad exception classification in `MonadAnchor.anchor_checkpoint()`

Discovered empirically while testing against an unreachable RPC: web3.py
7.16.0's request/response pipeline can raise exception types well outside
`(TimeExhausted, Web3Exception, OSError)` for a connection failure (a bare
`TypeError` from `eth_utils`' response decoder, observed directly). Rather
than maintain a brittle, ever-growing allowlist of exception types to
treat as transient, `anchor_checkpoint()` catches `Exception` broadly
(re-raising `AnchorConflictError` unchanged) and classifies everything
else as `TransientAnchorError`. This is the only classification that can
honour "Monad's unavailability must never block or crash anything"
without depending on correctly enumerating every failure mode a
third-party HTTP/RPC stack might produce.

### 9. web3.py's default HTTPProvider retry/backoff is left enabled

web3.py retries connection failures internally (5x, with backoff) on top
of the adapter's own `timeout_s`. An attempt to disable this via
`exception_retry_configuration=ExceptionRetryConfiguration(retries=0)`
was tried and reverted: in this installed web3.py version (7.16.0), it
broke response decoding even against a *working* local Anvil node (the
same `TypeError` from decision 8, but on the happy path too) — a real
library incompatibility, not a misconfiguration. Net effect: a single
`anchor_checkpoint()` call against a genuinely unreachable RPC can take
roughly 10-15 seconds before raising, and the worker's own
`retry_with_full_jitter` layers further attempts on top of that. Still
strictly bounded (proven in
`tests/integration/anchor/test_non_blocking.py`, never hangs forever),
just not sub-second — an accepted, documented trade-off rather than a
requirement violation, since nothing in the money/audit path is ever
exposed to this latency.

## Consequences

- A future phase wanting sub-second anchor-failure detection would need
  either a different HTTP transport for web3.py, a newer web3.py release,
  or a hand-rolled JSON-RPC client bypassing `HTTPProvider` entirely —
  none of which this phase's strictly-bounded scope justified building.
- If Monad Testnet is ever reset from genesis again (as documented on
  `docs.monad.xyz` — it was reset 2025-12-16), previously anchored
  checkpoints become unreachable at their old contract address; a fresh
  deployment and a new `MONAD_CONTRACT_ADDRESS` is the correct recovery,
  not a code change — the local hash chain remains the trustworthy source
  of truth regardless (ADR 0013).
- `ANCHOR_PROVIDER=noop` (default) is unaffected by any of the above: the
  worker never starts `_anchor_loop`, so none of these decisions have any
  runtime effect unless an operator explicitly opts in.

## Alternatives considered

- **Extend `Anchor.anchor_root()`'s signature** to carry
  `(audit_chain_id, start_seq, end_seq, merkle_root)` instead of adding a
  separate `anchor_checkpoint()` method. Rejected: would touch
  `application/ports.py`, diluting the port's deliberately minimal "the
  root, and only the root" contract (§16.1) for a concern
  (checkpoint-range-aware, retryable, idempotent on-chain submission)
  that is a materially different responsibility from "publish this root
  somewhere," and is better modelled as its own method on the concrete
  adapter, called only from the composition root (`actl.worker`) that
  already imports concrete infrastructure factories directly.
- **A generic new outbox-relay table**, matching `docs/architecture.md`
  §19's aspirational Redis Streams design. Rejected as out of this
  phase's strictly bounded scope: `audit_checkpoints` already has exactly
  the columns a poll-based outbox needs, and building a second, more
  general mechanism for one consumer would be speculative infrastructure
  P11 doesn't need.
