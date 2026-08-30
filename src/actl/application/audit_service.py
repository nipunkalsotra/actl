"""The append service (§16.1): the only path that writes to `audit_log`.

Single-writer serialisation via a Postgres advisory lock keyed by chain id
— without it, two concurrent appends can read the same prev_hash and fork
the chain (§16.1: "the classic silent bug in this pattern"). Runs entirely
inside the caller's UnitOfWork transaction: this module never begins or
commits a transaction itself, matching P2's "UnitOfWork is the only way
application code touches the database."

Checkpointing happens synchronously, in the same transaction as the entry
that crosses a checkpoint boundary — not a separate worker — which is what
makes "persist checkpoint metadata/root atomically with the relevant audit
state" trivially true rather than something a second process has to get
right. See docs/adr/0004-p3-trust-layer-decisions.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from actl.application.ports import Anchor
from actl.config import settings
from actl.domain.audit.canonical import JSONValue
from actl.domain.audit.chain import (
    GENESIS_PREV_HASH,
    compute_entry_hash,
    hex_prefixed,
    parse_hex_prefixed,
    payload_hash,
)
from actl.domain.audit.events import AuditAction
from actl.domain.audit.merkle import merkle_root
from actl.infrastructure.db.repositories.audit_checkpoints import AuditCheckpointRecord
from actl.infrastructure.db.repositories.audit_log import AuditLogRecord
from actl.infrastructure.db.uow import UnitOfWork
from actl.platform import metrics, tracing
from actl.platform.clock import Clock

DEFAULT_CHAIN_ID = "actl.audit_log"


async def append_entry(
    uow: UnitOfWork,
    *,
    trace_id: str,
    actor_type: str,
    actor_id: str,
    action: AuditAction,
    subject: dict[str, Any],
    payload: dict[str, Any],
    chain_id: str = DEFAULT_CHAIN_ID,
    anchor: Anchor | None = None,
) -> AuditLogRecord:
    """Append one entry to the chain and, if it lands on a checkpoint
    boundary, write that checkpoint too — all inside `uow`'s transaction.
    Caller commits; this function never does.

    `anchor` is the §16.1 stretch goal: this module depends only on the
    `Anchor` port, never on a concrete adapter — omit it (the default) for
    the same no-anchoring behaviour a `NoopAnchor` would give, or pass one
    explicitly (e.g. `infrastructure.anchor.noop.NoopAnchor()`) to make that
    choice visible at the call site."""
    with tracing.span("audit.append_entry", action=str(action)):
        await uow.audit_log.acquire_chain_lock(chain_id)

        tail = await uow.audit_log.get_tail()
        if tail is None:
            seq = 1
            prev_hash = GENESIS_PREV_HASH
        else:
            prev_seq, prev_entry_hash_hex = tail
            seq = prev_seq + 1
            prev_hash = parse_hex_prefixed(prev_entry_hash_hex)

        entry_hash = compute_entry_hash(prev_hash, payload)

        record = AuditLogRecord(
            seq=seq,
            trace_id=trace_id,
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            subject=subject,
            payload=payload,
            payload_hash=hex_prefixed(payload_hash(payload)),
            prev_hash=hex_prefixed(prev_hash),
            entry_hash=hex_prefixed(entry_hash),
        )
        await uow.audit_log.add_at_seq(seq, record)
        metrics.chain_length.set(seq)

        if seq % settings.audit_checkpoint_every == 0:
            await _write_checkpoint(uow, to_seq=seq, anchor=anchor)

        return record


async def _write_checkpoint(
    uow: UnitOfWork, *, to_seq: int, anchor: Anchor | None = None
) -> None:
    """Idempotent: a checkpoint already recorded for `to_seq` is left alone,
    so a retried/restarted caller can never produce a duplicate or a second,
    inconsistent root for the same segment."""
    if await uow.audit_checkpoints.get_by_to_seq(to_seq) is not None:
        return

    checkpoint_every = settings.audit_checkpoint_every
    from_seq = to_seq - checkpoint_every + 1
    entries = await uow.audit_log.list_range(from_seq, to_seq)
    if len(entries) != checkpoint_every:
        raise RuntimeError(
            f"checkpoint segment {from_seq}..{to_seq} expected {checkpoint_every} "
            f"entries, found {len(entries)}"
        )

    leaf_hashes = [parse_hex_prefixed(entry.entry_hash) for entry in entries]
    root_hex = hex_prefixed(merkle_root(leaf_hashes))

    # §16.1: "the root — and only the root — may be written." No business
    # data ever reaches the port. anchor_root() is called *before* the
    # checkpoint row is written so a real adapter's tx id can be persisted
    # alongside it in the same transaction; a None result (the no-op
    # default, or anchoring simply disabled) just leaves anchor_tx unset.
    anchor_tx = await anchor.anchor_root(root_hex) if anchor is not None else None

    await uow.audit_checkpoints.add(
        AuditCheckpointRecord(
            from_seq=from_seq, to_seq=to_seq, merkle_root=root_hex, anchor_tx=anchor_tx
        )
    )


@dataclass(frozen=True)
class ChainBreak:
    seq: int
    expected_entry_hash: str
    computed_entry_hash: str
    reason: str


@dataclass(frozen=True)
class ChainVerificationResult:
    """§16.2 / §28 P3: what `actl verify-chain` reports. `break_` is None on
    success; on failure it names the exact seq and the expected vs. actual
    hash (§16 doesn't ask for a field-level diff, and one isn't reliably
    derivable from a hash mismatch alone — see docs/adr/0004)."""

    ok: bool
    from_seq: int
    to_seq: int
    entries_verified: int
    checkpoints_matched: list[int] = field(default_factory=list)
    head_entry_hash: str | None = None
    break_: ChainBreak | None = None


async def verify_chain(uow: UnitOfWork, from_seq: int, to_seq: int) -> ChainVerificationResult:
    """Recompute every entry hash from `from_seq` to `to_seq`, validate
    prev_hash linkage (and genesis if from_seq == 1), and check any Merkle
    checkpoint whose to_seq falls inside the range. Read-only — never
    called from inside append_entry()'s write path."""
    entries = await uow.audit_log.list_range(from_seq, to_seq)
    checkpoints_by_to_seq = {c.to_seq: c for c in await uow.audit_checkpoints.list_all()}

    if from_seq == 1:
        prev_hash_bytes = GENESIS_PREV_HASH
    else:
        prior = await uow.audit_log.get_by_seq(from_seq - 1)
        if prior is None:
            return ChainVerificationResult(
                ok=False,
                from_seq=from_seq,
                to_seq=to_seq,
                entries_verified=0,
                break_=ChainBreak(
                    seq=from_seq - 1,
                    expected_entry_hash="<a preceding entry>",
                    computed_entry_hash="<not found>",
                    reason=f"cannot verify from seq={from_seq}: seq={from_seq - 1} is missing",
                ),
            )
        prev_hash_bytes = parse_hex_prefixed(prior.entry_hash)

    verified = 0
    checkpoints_matched: list[int] = []
    segment_hashes: list[bytes] = []
    expected_seq = from_seq

    for entry in entries:
        if entry.seq != expected_seq:
            return ChainVerificationResult(
                ok=False,
                from_seq=from_seq,
                to_seq=to_seq,
                entries_verified=verified,
                checkpoints_matched=checkpoints_matched,
                break_=ChainBreak(
                    seq=expected_seq,
                    expected_entry_hash="<row present>",
                    computed_entry_hash="<row missing>",
                    reason=f"sequence gap: expected seq={expected_seq}, found seq={entry.seq}",
                ),
            )

        recomputed = compute_entry_hash(prev_hash_bytes, cast(JSONValue, entry.payload))
        recomputed_hex = hex_prefixed(recomputed)
        expected_prev_hex = hex_prefixed(prev_hash_bytes)
        if entry.prev_hash != expected_prev_hex or entry.entry_hash != recomputed_hex:
            return ChainVerificationResult(
                ok=False,
                from_seq=from_seq,
                to_seq=to_seq,
                entries_verified=verified,
                checkpoints_matched=checkpoints_matched,
                break_=ChainBreak(
                    seq=entry.seq,
                    expected_entry_hash=entry.entry_hash,
                    computed_entry_hash=recomputed_hex,
                    reason="payload hash does not match the recorded entry_hash",
                ),
            )

        segment_hashes.append(recomputed)
        prev_hash_bytes = recomputed
        verified += 1
        expected_seq += 1

        checkpoint_every = settings.audit_checkpoint_every
        segment_start_seq = entry.seq - checkpoint_every + 1
        if entry.seq % checkpoint_every == 0 and segment_start_seq >= from_seq:
            checkpoint = checkpoints_by_to_seq.get(entry.seq)
            if checkpoint is not None:
                segment = segment_hashes[-checkpoint_every:]
                recomputed_root = hex_prefixed(merkle_root(segment))
                if recomputed_root != checkpoint.merkle_root:
                    return ChainVerificationResult(
                        ok=False,
                        from_seq=from_seq,
                        to_seq=to_seq,
                        entries_verified=verified,
                        checkpoints_matched=checkpoints_matched,
                        break_=ChainBreak(
                            seq=entry.seq,
                            expected_entry_hash=checkpoint.merkle_root,
                            computed_entry_hash=recomputed_root,
                            reason=f"merkle root mismatch for checkpoint ending at seq={entry.seq}",
                        ),
                    )
                checkpoints_matched.append(entry.seq)

    head = hex_prefixed(prev_hash_bytes) if entries else None
    return ChainVerificationResult(
        ok=True,
        from_seq=from_seq,
        to_seq=to_seq,
        entries_verified=verified,
        checkpoints_matched=checkpoints_matched,
        head_entry_hash=head,
    )


async def verify_chain_and_halt_on_failure(
    uow: UnitOfWork, from_seq: int, to_seq: int, clock: Clock
) -> ChainVerificationResult:
    """§20 F10: "Detection: Verifier ... Response: Halt all money
    actions." A thin wrapper, not a change to `verify_chain` itself (which
    stays pure/read-only, unmodified, so every existing P3 caller and test
    keeps its exact behaviour) -- this is the one place that ties a real
    integrity-verification run to the durable `integrity_halt` row (§28
    P9 production-readiness correction, docs/adr/0010 decision 16).
    `actl verify-chain` and the demo/chaos F10 scenario both call this
    instead of the bare `verify_chain`; any other caller that only wants
    the read-only check keeps calling `verify_chain` directly.

    Caller commits, matching every other write in this module (`append_
    entry`'s own "caller commits; this function never does") -- the trip
    must be durable the moment this returns, so every caller of this
    specific function is expected to commit immediately, not batch it
    with unrelated work."""
    result = await verify_chain(uow, from_seq, to_seq)
    if not result.ok:
        b = result.break_
        reason = b.reason if b is not None else "audit chain verification failed"
        await uow.integrity.trip(reason=reason, tripped_seq=b.seq if b else None, now=clock.now())
    return result
