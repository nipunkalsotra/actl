// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title AuditCheckpointAnchor
/// @notice §28 P11 / §16.1: publishes ACTL audit-checkpoint Merkle roots to
/// Monad Testnet, and only the roots -- (auditChainId, startSeq, endSeq,
/// merkleRoot). No user text, mandate bodies, payment identifiers, personal
/// data, or raw audit payloads ever reach this contract; the off-chain hash
/// chain (docs/adr/0013-hash-chain-over-blockchain.md) remains the system
/// of record, this is purely external timestamping evidence for it.
///
/// Deliberately minimal: no upgradeability, no token logic, no wallets, no
/// DeFi. One owner, one write path, one read path.
contract AuditCheckpointAnchor {
    struct Checkpoint {
        bytes32 merkleRoot;
        uint64 anchoredAt;
    }

    address public immutable owner;

    // auditChainId => startSeq => endSeq => Checkpoint. A zero merkleRoot
    // means "nothing anchored for this exact range yet" -- write-side
    // rejects a caller-supplied zero root (see InvalidRoot), so bytes32(0)
    // can never be a legitimately anchored value and is safe to use as
    // the "unset" sentinel on the read side.
    mapping(bytes32 => mapping(uint64 => mapping(uint64 => Checkpoint))) private _checkpoints;

    event CheckpointAnchored(
        bytes32 indexed auditChainId,
        uint64 indexed startSeq,
        uint64 indexed endSeq,
        bytes32 merkleRoot,
        uint64 anchoredAt
    );

    error NotOwner();
    error InvalidRoot();
    error InvalidRange();
    error ConflictingRoot(bytes32 existingRoot);

    constructor() {
        owner = msg.sender;
    }

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    /// @notice Anchor one checkpoint's Merkle root. Idempotent: resubmitting
    /// the exact same (auditChainId, startSeq, endSeq, merkleRoot) is a
    /// no-op success, never a revert or a duplicate event. A different root
    /// for an already-anchored range reverts with ConflictingRoot -- a
    /// permanent, on-chain-provable integrity finding.
    function anchor(bytes32 auditChainId, uint64 startSeq, uint64 endSeq, bytes32 merkleRoot)
        external
        onlyOwner
    {
        if (merkleRoot == bytes32(0)) revert InvalidRoot();
        if (endSeq < startSeq) revert InvalidRange();

        Checkpoint storage existing = _checkpoints[auditChainId][startSeq][endSeq];
        if (existing.merkleRoot != bytes32(0)) {
            if (existing.merkleRoot != merkleRoot) {
                revert ConflictingRoot(existing.merkleRoot);
            }
            return;
        }

        _checkpoints[auditChainId][startSeq][endSeq] =
            Checkpoint({merkleRoot: merkleRoot, anchoredAt: uint64(block.timestamp)});
        emit CheckpointAnchored(auditChainId, startSeq, endSeq, merkleRoot, uint64(block.timestamp));
    }

    /// @notice Third-party verification: anyone can read back the anchored
    /// root for a given range and compare it against their own locally
    /// recomputed checkpoint root. Returns (bytes32(0), 0) if unanchored.
    function getCheckpoint(bytes32 auditChainId, uint64 startSeq, uint64 endSeq)
        external
        view
        returns (bytes32 merkleRoot, uint64 anchoredAt)
    {
        Checkpoint storage c = _checkpoints[auditChainId][startSeq][endSeq];
        return (c.merkleRoot, c.anchoredAt);
    }
}
