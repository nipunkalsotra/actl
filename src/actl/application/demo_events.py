"""Real, structured milestone events for a live Demo Lab ("Trust Lab") run.

Every event a `DemoRunRecorder` emits is derived from a value the scenario
just actually computed or persisted (a just-appended real `AuditLogRecord`,
a real ledger balance re-read, a real gate/policy verdict) -- never a
fabricated timestamp, id, hash, or amount. `demo.py`'s scenario functions
accept an optional recorder (default: `NULL_RECORDER`, a real no-op) so
every existing CLI/golden-trace/test call path that omits one is
byte-for-byte unaffected; only `application.demo_runs`'s live-run
orchestration ever supplies a real one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class DemoEventStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    BLOCKED = "blocked"
    FAILED = "failed"
    COMPENSATED = "compensated"


@dataclass(frozen=True)
class DemoEvidence:
    """Every field is optional and omitted from the JSON response when
    unset (see `demo_runs.event_json`) -- an event only ever carries the
    evidence that specific real step actually produced."""

    order_id: str | None = None
    quote_id: str | None = None
    catalog_version: int | None = None
    gate: str | None = None
    reason_code: str | None = None
    payment_state: str | None = None
    reserved_balance_minor: int | None = None
    released_balance_minor: int | None = None
    audit_seq: int | None = None
    entry_hash_prefix: str | None = None
    checkpoint_status: str | None = None


@dataclass(frozen=True)
class DemoEvent:
    seq: int
    ts: datetime
    phase: str
    kind: str
    title: str
    detail: str
    status: DemoEventStatus
    evidence: DemoEvidence = field(default_factory=DemoEvidence)


class DemoRunRecorder:
    """Appends events to one live run's own list, in arrival order. `seq`
    here is this run's own 1-based event counter -- unrelated to (but
    often carrying, via `evidence.audit_seq`) the audit chain's own global
    seq numbers."""

    def __init__(self) -> None:
        self.events: list[DemoEvent] = []

    def emit(
        self,
        *,
        phase: str,
        kind: str,
        title: str,
        detail: str,
        status: DemoEventStatus,
        evidence: DemoEvidence | None = None,
    ) -> DemoEvent:
        event = DemoEvent(
            seq=len(self.events) + 1,
            ts=datetime.now(UTC),
            phase=phase,
            kind=kind,
            title=title,
            detail=detail,
            status=status,
            evidence=evidence or DemoEvidence(),
        )
        self.events.append(event)
        return event


class _NullRecorder(DemoRunRecorder):
    """Same interface, appends nowhere -- the default for every existing
    caller (`actl demo`, golden-trace generation, chaos/unit tests) that
    never asked for live event tracking in the first place."""

    def emit(
        self,
        *,
        phase: str,
        kind: str,
        title: str,
        detail: str,
        status: DemoEventStatus,
        evidence: DemoEvidence | None = None,
    ) -> DemoEvent:
        return DemoEvent(
            seq=0, ts=datetime.now(UTC), phase=phase, kind=kind, title=title,
            detail=detail, status=status, evidence=evidence or DemoEvidence(),
        )


NULL_RECORDER = _NullRecorder()
