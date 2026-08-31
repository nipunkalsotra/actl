"""§28 P11: the real (optional) Anchor adapter -- publishes ACTL
audit-checkpoint Merkle roots to a Monad Testnet AuditCheckpointAnchor
contract (chain/src/AuditCheckpointAnchor.sol). Testnet only: `infrastructure.
anchor.factory.build_anchor_worker` refuses to construct this adapter for
any chain id other than MONAD_TESTNET_CHAIN_ID.

Anchors only (audit_chain_id, start_seq, end_seq, merkle_root) -- never
business data, user text, mandate bodies, payment identifiers, personal
data, or raw audit payloads reach this module or the chain (§16.1).

`anchor_root()` exists so this class genuinely satisfies `application.
ports.Anchor`'s structural protocol, but is deliberately never wired into
`application.audit_service.append_entry`'s synchronous checkpoint path --
doing so would tie audit-append latency (and, transitively, every gate/
ledger/saga/checkout action inside that same transaction) to Monad
Testnet's availability, which §28 P11's non-negotiable rules forbid. Real
submission happens from `actl.worker`'s async `_anchor_loop`, calling
`anchor_checkpoint()` directly, entirely outside any audit-append
transaction. See docs/adr/0016-p11-monad-anchoring-decisions.md for why
this is a deliberate, documented deviation from ADR 0004 decision 9's
original "no application/ports.py change" prediction (it holds -- ports.py
is untouched -- but not for the reason ADR 0004 assumed).

All web3 I/O is synchronous (web3.py's default HTTPProvider); every public
async method offloads the blocking call to a thread via `asyncio.to_thread`
so it never blocks the worker's event loop (and, transitively, the
webhook/reconcile loops running concurrently in the same process).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from eth_account import Account
from web3 import Web3
from web3.exceptions import ContractCustomError

from actl.domain.audit.chain import parse_hex_prefixed
from actl.platform.errors import ExternalServiceError

MONAD_TESTNET_CHAIN_ID = 10143

_ANCHOR_ABI: list[dict[str, object]] = [
    {
        "type": "function",
        "name": "anchor",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "auditChainId", "type": "bytes32"},
            {"name": "startSeq", "type": "uint64"},
            {"name": "endSeq", "type": "uint64"},
            {"name": "merkleRoot", "type": "bytes32"},
        ],
        "outputs": [],
    },
    {
        "type": "function",
        "name": "getCheckpoint",
        "stateMutability": "view",
        "inputs": [
            {"name": "auditChainId", "type": "bytes32"},
            {"name": "startSeq", "type": "uint64"},
            {"name": "endSeq", "type": "uint64"},
        ],
        "outputs": [
            {"name": "merkleRoot", "type": "bytes32"},
            {"name": "anchoredAt", "type": "uint64"},
        ],
    },
    {
        "type": "error",
        "name": "ConflictingRoot",
        "inputs": [{"name": "existingRoot", "type": "bytes32"}],
    },
    {"type": "error", "name": "NotOwner", "inputs": []},
    {"type": "error", "name": "InvalidRoot", "inputs": []},
    {"type": "error", "name": "InvalidRange", "inputs": []},
]

_ZERO_ROOT = bytes(32)


class TransientAnchorError(ExternalServiceError):
    """RPC timeout / connection failure / dropped-and-retried transaction --
    safe to retry; the checkpoint stays 'unanchored' and is retried on the
    next worker tick (§28 P11 instruction 4)."""

    reason_code = "ANCHOR_TRANSIENT"


class AnchorConflictError(ExternalServiceError):
    """The contract already holds a DIFFERENT root for this exact
    (auditChainId, startSeq, endSeq) range -- never retried; a permanent
    integrity finding, surfaced to metrics/audit/the runbook (§28 P11
    instruction 4), never silently swallowed."""

    reason_code = "ANCHOR_ROOT_CONFLICT"


@dataclass(frozen=True)
class AnchorSubmission:
    chain_id: int
    contract_address: str
    already_anchored: bool
    tx_hash: str | None = None


class MonadAnchor:
    def __init__(
        self,
        *,
        rpc_url: str,
        chain_id: int,
        contract_address: str,
        keystore_path: str,
        keystore_password: str,
        audit_chain_id: str,
        timeout_s: float = 10.0,
    ) -> None:
        self._chain_id = chain_id
        self._contract_address = Web3.to_checksum_address(contract_address)
        self._audit_chain_id_bytes32 = Web3.keccak(text=audit_chain_id)
        self._timeout_s = timeout_s
        # web3.py's HTTPProvider retries connection failures internally by
        # default (5x, with backoff) on top of `timeout_s` -- discovered
        # empirically (a 2s connect timeout took ~12s to actually fail).
        # Tried disabling it via exception_retry_configuration(retries=0);
        # that broke response decoding even against a *working* node in
        # this installed web3.py version (7.16.0) -- a real incompatibility,
        # not a misconfiguration, so the default is kept. Net effect: a
        # single anchor_checkpoint() call against an unreachable RPC can
        # take ~10-15s before raising, and worker._anchor_checkpoint_with_
        # retry layers its own retries on top of that -- bounded (never
        # hangs forever, proven in tests/integration/anchor/
        # test_non_blocking.py), just not sub-second.
        self._w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": timeout_s}))
        self._contract = self._w3.eth.contract(address=self._contract_address, abi=_ANCHOR_ABI)

        keystore_json = Path(keystore_path).read_text(encoding="utf-8")
        # Decrypted once, held only in process memory for the adapter's
        # lifetime -- never logged, never re-serialised, never written
        # back to disk (§28 P11 non-negotiable safety rules).
        private_key = Account.decrypt(keystore_json, keystore_password)
        self._account = Account.from_key(private_key)

    @property
    def address(self) -> str:
        return str(self._account.address)

    @property
    def contract_address(self) -> str:
        return self._contract_address

    async def anchor_root(self, merkle_root: str) -> str | None:
        raise NotImplementedError(
            "MonadAnchor.anchor_root() is intentionally not the real anchoring "
            "path -- it lacks the (start_seq, end_seq) range the on-chain "
            "contract requires, and calling it from application.audit_service."
            "append_entry's synchronous checkpoint path would block audit "
            "appends on Monad's availability. Use anchor_checkpoint() from an "
            "async worker loop instead (actl.worker._anchor_loop). See "
            "docs/adr/0016-p11-monad-anchoring-decisions.md."
        )

    async def anchor_checkpoint(
        self, *, start_seq: int, end_seq: int, merkle_root_hex: str
    ) -> AnchorSubmission:
        """Idempotent, retryable submission of one checkpoint. Reads the
        contract's current state for this exact range before writing
        anything: an identical root already stored is treated as success
        with no transaction submitted (saves gas, and is what makes a
        retried delivery never submit a duplicate anchor); a *different*
        root already stored raises AnchorConflictError, never retried."""
        merkle_root_bytes = parse_hex_prefixed(merkle_root_hex)
        try:
            return await self._anchor_checkpoint_sync(start_seq, end_seq, merkle_root_bytes)
        except AnchorConflictError:
            raise
        except Exception as exc:
            # Deliberately broad, not just (TimeExhausted, Web3Exception,
            # OSError): an unreachable/misbehaving RPC can surface as
            # near-arbitrary exceptions from deep inside web3.py's request/
            # response pipeline (confirmed empirically -- a bare TypeError
            # from eth_utils' response decoder, not any of those three
            # types, when the connection fails a particular way). §28 P11's
            # non-negotiable rule is that Monad's unavailability must never
            # block or crash anything; "unexpected error -> treat as
            # transient and let the worker's own retry/breaker policy
            # decide" is the only classification that can honour that
            # without maintaining an exhaustive, ever-growing exception
            # allowlist for a third-party HTTP/RPC stack.
            raise TransientAnchorError(f"monad anchor submission failed: {exc}") from exc

    async def _anchor_checkpoint_sync(
        self, start_seq: int, end_seq: int, merkle_root_bytes: bytes
    ) -> AnchorSubmission:
        return await asyncio.to_thread(
            self._anchor_checkpoint_blocking, start_seq, end_seq, merkle_root_bytes
        )

    def _get_checkpoint_raw(self, start_seq: int, end_seq: int) -> tuple[bytes, int]:
        result: tuple[bytes, int] = self._contract.functions.getCheckpoint(
            self._audit_chain_id_bytes32, start_seq, end_seq
        ).call()
        return result

    def _anchor_checkpoint_blocking(
        self, start_seq: int, end_seq: int, merkle_root_bytes: bytes
    ) -> AnchorSubmission:
        existing_root, _existing_anchored_at = self._get_checkpoint_raw(start_seq, end_seq)

        if existing_root != _ZERO_ROOT:
            if existing_root == merkle_root_bytes:
                return AnchorSubmission(
                    chain_id=self._chain_id,
                    contract_address=self._contract_address,
                    already_anchored=True,
                )
            raise AnchorConflictError(
                "on-chain root disagrees with the local checkpoint for the same "
                f"range (start_seq={start_seq}, end_seq={end_seq}): "
                f"existing=0x{existing_root.hex()} local=0x{merkle_root_bytes.hex()}",
                details={
                    "start_seq": start_seq,
                    "end_seq": end_seq,
                    "existing_root": f"0x{existing_root.hex()}",
                    "local_root": f"0x{merkle_root_bytes.hex()}",
                },
            )

        try:
            nonce = self._w3.eth.get_transaction_count(self._account.address, "pending")
            tx = self._contract.functions.anchor(
                self._audit_chain_id_bytes32, start_seq, end_seq, merkle_root_bytes
            ).build_transaction(
                {"chainId": self._chain_id, "from": self._account.address, "nonce": nonce}
            )
            signed = self._account.sign_transaction(tx)
            tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = self._w3.eth.wait_for_transaction_receipt(
                tx_hash, timeout=self._timeout_s
            )
        except ContractCustomError as exc:
            # Defense in depth against a race with another writer between
            # the read above and this write -- this system runs one worker
            # process (§28 ADR 0011 "one deployable process"), so this path
            # is not expected to trigger in normal operation.
            if "ConflictingRoot" in str(exc):
                raise AnchorConflictError(f"on-chain conflict on submission: {exc}") from exc
            raise

        if receipt["status"] != 1:
            raise TransientAnchorError(f"anchor transaction reverted: 0x{tx_hash.hex()}")

        return AnchorSubmission(
            chain_id=self._chain_id,
            contract_address=self._contract_address,
            already_anchored=False,
            tx_hash=f"0x{tx_hash.hex()}",
        )

    def read_checkpoint(
        self, *, start_seq: int, end_seq: int
    ) -> tuple[str | None, int]:
        """Opt-in Testnet verifier support (§28 P11 instruction 5): read
        the on-chain root for a range without submitting anything. Returns
        (None, 0) if unanchored, else ("0x"-prefixed root, anchored_at
        unix timestamp)."""
        root, anchored_at = self._get_checkpoint_raw(start_seq, end_seq)
        if root == _ZERO_ROOT:
            return None, 0
        return f"0x{root.hex()}", anchored_at
