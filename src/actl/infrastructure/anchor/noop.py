"""§28 P3: the default Anchor adapter. Real-chain integration (Monad
testnet or otherwise) is an explicit stretch goal kept out of this phase —
no network calls, no credentials, nothing to configure. `anchor_root`
always returns None, meaning "not anchored"; the local hash chain remains
fully tamper-evident on its own (§16.1 RISK/GUARD)."""

from __future__ import annotations


class NoopAnchor:
    async def anchor_root(self, merkle_root: str) -> str | None:
        return None
