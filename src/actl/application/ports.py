"""Ports (protocols) the application layer depends on; infrastructure
supplies the concrete adapters. Accumulates over phases (§25) — P3 adds
`Anchor` only; `PaymentProvider`, `LLMClient`, `Clock`, `EventBus` land with
whichever later phase first needs to inject or mock one (same reasoning as
ADR 0003 decision 8 for `UnitOfWork` not getting a port until P6)."""

from __future__ import annotations

from typing import Protocol


class Anchor(Protocol):
    """§16.1: optional external timestamping for a Merkle checkpoint root —
    "the root, and only the root," never business data. A no-op default
    (infrastructure/anchor/noop.py) means the stretch goal of real-chain
    anchoring can never block the critical path (§28 P3 Key decision)."""

    async def anchor_root(self, merkle_root: str) -> str | None:
        """Publish `merkle_root` externally, returning a reference (e.g. a
        transaction id) once anchored, or None if this adapter doesn't
        anchor at all. None is a normal, expected result — not a failure."""
        ...
