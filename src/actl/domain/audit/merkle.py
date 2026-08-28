"""Merkle checkpoints (§16.1, §16 Figure 16.1): a root computed over each
AUDIT_CHECKPOINT_EVERY-entry segment, so verifying a recent segment doesn't
require rehashing the entire history.

§16 specifies the checkpoint cadence ("every 64 entries a root is computed
and stored") and the chain's own hash formula, but not a byte-exact
leaf/node construction for the tree itself. This implements the RFC 6962
(Certificate Transparency) binary Merkle tree: domain-separated leaf vs.
internal-node hashing (0x00 / 0x01 prefixes) closes the classic
second-preimage ambiguity where an internal node's hash could otherwise be
presented as a valid leaf. See docs/adr/0004-p3-trust-layer-decisions.md.
"""

from __future__ import annotations

import hashlib

_LEAF_PREFIX = b"\x00"
_NODE_PREFIX = b"\x01"


def leaf_hash(entry_hash: bytes) -> bytes:
    return hashlib.sha256(_LEAF_PREFIX + entry_hash).digest()


def _node_hash(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(_NODE_PREFIX + left + right).digest()


def merkle_root(entry_hashes: list[bytes]) -> bytes:
    """`entry_hashes` in seq order for one checkpoint segment. RFC 6962: a
    lone trailing node at any level is promoted unchanged rather than
    duplicated — duplication has known malleability issues (e.g. the
    Bitcoin CVE-2012-2459 duplicate-transaction attack)."""
    if not entry_hashes:
        raise ValueError("cannot compute a Merkle root over zero entries")
    level = [leaf_hash(h) for h in entry_hashes]
    while len(level) > 1:
        next_level = []
        i = 0
        while i < len(level):
            if i + 1 < len(level):
                next_level.append(_node_hash(level[i], level[i + 1]))
                i += 2
            else:
                next_level.append(level[i])
                i += 1
        level = next_level
    return level[0]
