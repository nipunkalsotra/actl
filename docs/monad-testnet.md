# Monad Testnet anchoring (§28 P11, optional)

Publishes ACTL audit-checkpoint Merkle roots — and only the roots — to a
Monad Testnet smart contract, as external timestamping evidence for the
hash chain §16.1/ADR [`0013`](adr/0013-hash-chain-over-blockchain.md)
already builds. `NoopAnchor` remains the default; this entire subsystem is
optional, off by default, asynchronous, and non-blocking. See ADR
[`0016`](adr/0016-p11-monad-anchoring-decisions.md) for the design
rationale.

## What gets anchored, and what never does

**Anchored (on-chain, public):**
- `audit_chain_id` (as `keccak256(chain_id_string)`, a `bytes32`)
- The checkpoint's `startSeq`/`endSeq` (the segment of `audit_log` rows
  the root covers)
- `merkleRoot` — the SHA-256 Merkle root over that segment's entry
  hashes, computed exactly as the local hash chain already computes it
  (`domain/audit/merkle.py`)

**Never anchored, under any configuration:** user text, mandate bodies,
payment identifiers, personal data, secrets, private keys, or any raw
audit payload. The `anchor()` contract call takes exactly four scalar
arguments (`bytes32, uint64, uint64, bytes32`) — there is no code path
that could serialize more onto it.

## Testnet only — non-negotiable

This build refuses to anchor to anything but Monad Testnet, mechanically:
`infrastructure/anchor/factory.py::build_anchor_worker` raises
`SystemExit` at construction if `MONAD_CHAIN_ID` is ever set to anything
other than `10143`. There is no mainnet code path, no mainnet RPC
default, and no configuration that bypasses this check.

## Official sources consulted (2026-08-31)

- Network configuration (chain id, RPC, explorers, faucet):
  [docs.monad.xyz/developer-essentials/testnet](https://docs.monad.xyz/developer-essentials/testnet)
- Foundry deployment guide:
  [docs.monad.xyz/guides/deploy-smart-contract/foundry](https://docs.monad.xyz/guides/deploy-smart-contract/foundry)
- Contract verification guide:
  [docs.monad.xyz/guides/verify-smart-contract/foundry](https://docs.monad.xyz/guides/verify-smart-contract/foundry)
- Sourcify verifier endpoint (BlockVision, referenced by the official
  verification guide): `https://sourcify-api-monad.blockvision.org/`

Confirmed values: chain id `10143` (`0x27DF`), RPC
`https://testnet-rpc.monad.xyz`, explorers `testnet.monadvision.com` /
`testnet.monadscan.com`, faucet `https://faucet.monad.xyz` — all match
this build's `.env.example` defaults exactly.

## Architecture: why this never blocks money actions

`application/audit_service.py` and `application/ports.py` are untouched
by this feature. Every real call site of `append_entry()` still omits
`anchor=` exactly as before P11 — `_write_checkpoint` writes a checkpoint
row with `anchor_status='unanchored'` and returns; it never makes an RPC
call. A completely separate, opt-in background loop in `actl.worker`
(`_anchor_loop`, only started when `ANCHOR_PROVIDER=monad`) polls
`audit_checkpoints` for `anchor_status='unanchored'` rows — that table
itself *is* the outbox — and submits each one with the platform's
existing retry (`platform/retry.py`, full-jitter exponential backoff) and
circuit-breaker (`platform/breaker.py`) machinery, the same primitives
already used for Razorpay calls. See ADR
[`0016`](adr/0016-p11-monad-anchoring-decisions.md) for why this is a
deliberate, documented deviation from ADR 0004's original prediction that
the Anchor port itself would carry real anchoring.

Concretely: a payment, ledger reservation, saga step, checkout, or audit
append can never wait on or fail because of Monad — there is no import,
no call, no shared transaction between that code and anything in
`infrastructure/anchor/`. `tests/integration/anchor/test_non_blocking.py`
proves this by running a real demo scenario with `ANCHOR_PROVIDER=monad`
pointed at an unreachable host, and separately proves `MonadAnchor` and
`worker._anchor_tick` fail in bounded time rather than hanging.

## Failure and retry behaviour

| Situation | Behaviour |
|---|---|
| RPC timeout / connection error | `TransientAnchorError` — the checkpoint stays `unanchored`, retried by `retry_with_full_jitter` within the same tick (`MAX_RETRY_ATTEMPTS`), then again on the next 15s tick. `anchor_attempts`/`anchor_last_error` are recorded for diagnostics. |
| Repeated failures | The per-process `CircuitBreaker(name="monad-anchor")` opens after 5 consecutive failures and stays open for its recovery timeout, so a genuinely down RPC doesn't retry-storm — every other checkpoint in that tick, and every other worker loop (webhook, reconcile), is completely unaffected. |
| Identical checkpoint resubmitted | Idempotent success, no transaction submitted — `MonadAnchor` reads `getCheckpoint()` before writing; a matching on-chain root short-circuits to `already_anchored=True`. The contract itself is also idempotent for a genuine race (same submission twice). |
| A *different* root already anchored for the same range | `AnchorConflictError` — permanent, never retried. The checkpoint's `anchor_status` becomes `'conflict'`, `actl_anchor_submissions_total{outcome="conflict"}` increments, and an ERROR-level log line is emitted. Treat this as an F10-class integrity event (see the runbook). |
| `ANCHOR_PROVIDER=noop` (default) | The worker never starts the anchor loop at all — zero RPC calls, zero file reads, zero validation. Byte-for-byte the same behaviour as before P11 existed. |

## Verifying a checkpoint root (third party)

**Offline** (no dependency on this feature at all): `make bundle` then
`python3 audit_bundle/verify_bundle.py` — unchanged by P11, still the
primary, trustless verification path (§16.2, `docs/architecture.md`).

**On-chain, opt-in** — anyone can call the deployed contract's public
`getCheckpoint(bytes32 auditChainId, uint64 startSeq, uint64 endSeq)`
directly (e.g. via `cast call`, a block explorer's "Read Contract" tab,
or any web3 client) and compare the returned root against the offline
bundle's own checkpoint root for the same range — no ACTL code required.

**Using this repo's CLI** (requires local `ANCHOR_PROVIDER=monad`
configuration):

```
uv run python -m actl.cli verify-anchor --to <checkpoint_to_seq>
```

Reads the local checkpoint for that `to_seq`, queries the deployed
contract's `getCheckpoint`, and reports `VERIFIED` or `FAIL` with both
roots printed. Refuses to run (prints the exact reason, exits non-zero)
if `ANCHOR_PROVIDER` isn't `monad` or the checkpoint doesn't exist
locally yet — never a fabricated result.

## Switching back to NoopAnchor

Set `ANCHOR_PROVIDER=noop` (or leave it unset — that's the default) and
restart. No other change is needed; every `MONAD_*` variable is simply
ignored.

## Deployment (opt-in, keystore-based, never a plaintext key)

All of this is manual and explicit — nothing in this repository deploys,
funds a wallet, or submits a transaction on its own.

### 1. Prerequisites

- Foundry (`forge`, `cast`) — this build's `chain/foundry.toml` was
  written and tested against forge `1.7.1`; forge `1.8+` additionally
  supports `network = "monad"` in `foundry.toml` for Monad-aware
  compilation/simulation (commented out in this repo's config with
  instructions to re-enable after upgrading).
- Testnet MON from the official faucet:
  [faucet.monad.xyz](https://faucet.monad.xyz)

### 2. Create an encrypted keystore (never a plaintext private key)

```
cast wallet import monad-deployer --private-key <your-private-key>
# or, to generate a brand new key:
cast wallet import monad-deployer --private-key $(cast wallet new | grep 'Private key:' | awk '{print $3}')
cast wallet address --account monad-deployer   # prints the address to fund from the faucet
```

This writes an encrypted V3 keystore (Foundry's own format, the same one
`eth_account.Account.decrypt` reads) under `~/.foundry/keystores/`,
prompting for a password interactively — the password is never written
to disk or passed as a CLI argument.

### 3. Deploy (opt-in command, never run implicitly)

```
cd chain
forge script script/DeployAuditCheckpointAnchor.s.sol \
  --account monad-deployer --broadcast --rpc-url https://testnet-rpc.monad.xyz
```

Prints the deployed contract address — copy it into your local, ignored
`.env` as `MONAD_CONTRACT_ADDRESS`.

### 4. Verify the contract on-chain (optional, for public inspection)

```
forge verify-contract <contract_address> AuditCheckpointAnchor \
  --chain 10143 --verifier sourcify \
  --verifier-url https://sourcify-api-monad.blockvision.org/
```

### 5. Configure the backend and confirm before running the worker

Fill in your local, ignored `.env`:

```
ANCHOR_PROVIDER=monad
MONAD_RPC_URL=https://testnet-rpc.monad.xyz
MONAD_CHAIN_ID=10143
MONAD_CONTRACT_ADDRESS=<from step 3>
MONAD_KEYSTORE_PATH=/home/you/.foundry/keystores/monad-deployer
MONAD_KEYSTORE_PASSWORD=<the password you set in step 2>
```

`MONAD_PRIVATE_KEY` is never a variable this build reads, anywhere — only
`MONAD_KEYSTORE_PATH` + `MONAD_KEYSTORE_PASSWORD`. Then start
`python -m actl.worker`: it fails closed at startup
(`infrastructure/anchor/factory.py`) with an exact, actionable error if
any of the above is missing, malformed, or the keystore file doesn't
exist — never a silent no-op, never a fake success.

If you haven't done the above yet, don't try to run the worker with
`ANCHOR_PROVIDER=monad` — it will refuse to start, correctly, and tell
you exactly which variable is missing.

## Live Monad Testnet proof

This has been run for real, once, against live Monad Testnet — not just
tested against Anvil. Public values only:

- **Network:** Monad Testnet, chain id `10143`
- **`AuditCheckpointAnchor` contract:** `0x551983E7b577Eb2FAF3163BCA9a5d4ACfB577C1B`
  ([explorer](https://testnet.monadscan.com/address/0x551983E7b577Eb2FAF3163BCA9a5d4ACfB577C1B))
- **First ACTL checkpoint anchor transaction:** `0x8010cdf387dc6890126c4f4c2ff7abb84411bd260604157ad0b11e473737ff47`
  ([explorer](https://testnet.monadscan.com/tx/0x8010cdf387dc6890126c4f4c2ff7abb84411bd260604157ad0b11e473737ff47))
- **Anchored range:** seq 25–32
- **Merkle root:** `sha256:6c66289f039389958440d5abd4704879591264e65e657a445622598890f6d58b`

That transaction stores only the audit-chain identifier, the sequence
range, and the Merkle root on Monad Testnet — never payment data,
customer data, raw audit payloads, credentials, or PII, matching "What
gets anchored, and what never does" above exactly.

Anchoring remains optional and non-blocking: `ANCHOR_PROVIDER=noop` is
still this repository's committed default, and nothing above changes
that — this proof was run with a local, gitignored `.env` override, not
a change to any committed default.
