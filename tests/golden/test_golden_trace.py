"""§28 P3 exit criteria: test_golden_trace_hashes_match_committed_fixture.

"Golden trace hashes are byte-identical across two machines or two clean
runs" (P3 blocker) — a fixed, timestamp-free, randomness-free sequence of
payloads is committed with its expected hashes precomputed once; this test
recomputes them from scratch via the same pure domain functions and asserts
byte-identical output. No database, no clock — a regression here means the
canonicalisation or hashing logic itself changed output, not that some
runtime environment differed.
"""

from __future__ import annotations

import json
from pathlib import Path

from actl.domain.audit.chain import (
    GENESIS_PREV_HASH,
    compute_entry_hash,
    hex_prefixed,
    payload_hash,
)
from actl.domain.audit.merkle import merkle_root

FIXTURE_PATH = (
    Path(__file__).parent.parent.parent / "fixtures/golden_traces/audit_chain_golden.json"
)


def test_golden_trace_hashes_match_committed_fixture() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text())

    prev_hash = GENESIS_PREV_HASH
    entry_hashes = []
    for i, expected in enumerate(fixture["entries"]):
        payload = expected["payload"]

        recomputed_payload_hash = hex_prefixed(payload_hash(payload))
        assert recomputed_payload_hash == expected["payload_hash"], f"entry {i}: payload_hash"

        assert hex_prefixed(prev_hash) == expected["prev_hash"], f"entry {i}: prev_hash"

        entry_hash = compute_entry_hash(prev_hash, payload)
        assert hex_prefixed(entry_hash) == expected["entry_hash"], f"entry {i}: entry_hash"

        entry_hashes.append(entry_hash)
        prev_hash = entry_hash

    assert hex_prefixed(prev_hash) == fixture["head_entry_hash"]
    assert hex_prefixed(merkle_root(entry_hashes)) == fixture["merkle_root"]


def test_golden_trace_is_reproducible_from_a_fresh_process() -> None:
    """Same fixture, recomputed a second, independent time in this test run
    — "byte-identical across two clean runs" within one process at least;
    the real cross-machine claim is that this committed fixture itself was
    generated once and never hand-edited."""
    fixture = json.loads(FIXTURE_PATH.read_text())

    def recompute() -> str:
        prev_hash = GENESIS_PREV_HASH
        for entry in fixture["entries"]:
            prev_hash = compute_entry_hash(prev_hash, entry["payload"])
        return hex_prefixed(prev_hash)

    assert recompute() == recompute() == fixture["head_entry_hash"]
