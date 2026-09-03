"""Live, pollable Demo Lab ("Trust Lab") runs.

A thin orchestration layer over `application.demo`: starts a scenario (or
the `verify_chain` check) as a background asyncio task against a real
database, and keeps an in-memory, process-local record of its real
progress (`DemoRun`) that a judge's browser polls via
`GET /merchant/v1/demo-runs/{run_id}`.

In-memory, not a new Postgres table: this is transient UI-progress state
for a single guarded, local/CI-only feature (never the persistent judge-
facing deployment -- see `_require_safe_demo_environment` in
`interfaces.http.routers.merchant`), not durable business data. Nothing
here needs to survive a process restart or be visible across processes,
unlike e.g. the durable `integrity_halt` row (docs/adr/0010 decision 16),
which is a cross-process security signal -- a fundamentally different
requirement.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application.audit_service import verify_chain
from actl.application.demo import DemoResult, UnknownScenario, run_scenario
from actl.application.demo_events import DemoEvent, DemoEventStatus, DemoEvidence, DemoRunRecorder
from actl.infrastructure.db.uow import UnitOfWork
from actl.platform.ids import new_id

logger = structlog.get_logger(__name__)

VERIFY_CHAIN_RUN_KIND = "verify_chain"
RUNNABLE_KINDS = ("stale_price", "declined", "llm_down", VERIFY_CHAIN_RUN_KIND)


class DemoRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"


@dataclass
class DemoRun:
    run_id: str
    scenario: str
    status: DemoRunStatus
    started_at: datetime
    completed_at: datetime | None = None
    events: list[DemoEvent] = field(default_factory=list)
    result: DemoResult | None = None
    order_id: str | None = None
    error: str | None = None


_RUNS: dict[str, DemoRun] = {}
# asyncio.create_task's own docs: hold a strong reference or the task can
# be garbage-collected mid-run. Discarded from this set in its own done
# callback once it finishes, so this stays bounded to in-flight runs only.
_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()


def get_run(run_id: str) -> DemoRun | None:
    return _RUNS.get(run_id)


async def start_demo_run(
    scenario: str, session_factory: async_sessionmaker[AsyncSession]
) -> DemoRun:
    """Creates the run record and hands the real execution to a background
    task immediately -- the HTTP handler returns `run` (status=queued)
    without waiting for it, so the first poll can already be in flight
    while the scenario is still running."""
    if scenario not in RUNNABLE_KINDS:
        raise UnknownScenario(scenario)
    run = DemoRun(
        run_id=new_id("run"), scenario=scenario,
        status=DemoRunStatus.QUEUED, started_at=datetime.now(UTC),
    )
    _RUNS[run.run_id] = run
    task = asyncio.create_task(_execute(run, session_factory))
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return run


async def _execute(run: DemoRun, session_factory: async_sessionmaker[AsyncSession]) -> None:
    run.status = DemoRunStatus.RUNNING
    recorder = DemoRunRecorder()
    try:
        if run.scenario == VERIFY_CHAIN_RUN_KIND:
            result = await _run_verify_chain(session_factory, recorder)
        else:
            result = await run_scenario(
                run.scenario, session_factory, run_id=run.run_id, recorder=recorder
            )
        run.result = result
        run.order_id = result.order_id
        run.status = DemoRunStatus.PASSED
    except Exception as exc:  # a demo run must never crash the process
        logger.exception("demo_run.failed", run_id=run.run_id, scenario=run.scenario)
        run.error = f"{type(exc).__name__}: this run failed -- see server logs for detail."
        run.status = DemoRunStatus.FAILED
    finally:
        run.events = recorder.events
        run.completed_at = datetime.now(UTC)


def _anchor_detail(anchor_status: str) -> str:
    if anchor_status == "anchored":
        return "Anchored on Monad Testnet."
    if anchor_status == "conflict":
        return "Anchor conflict detected -- local root does not match the on-chain record."
    return "Not anchored (ANCHOR_PROVIDER=noop by default)."


async def _run_verify_chain(
    session_factory: async_sessionmaker[AsyncSession], recorder: DemoRunRecorder
) -> DemoResult:
    """Real, non-halting verification of the whole current chain -- same
    verifier and same `from_seq=1` scope as `POST /merchant/v1/demo/
    verify-chain`. Narrated as a real event sequence instead of a single
    final line: scan start, entries verified, each checkpoint actually
    covered by this range with its own real anchor_status (never claiming
    "anchored" unless a checkpoint's own row genuinely says so), then the
    real terminal verdict."""
    async with UnitOfWork(session_factory) as uow:
        tail = await uow.audit_log.get_tail()
    if tail is None:
        recorder.emit(
            phase="verify", kind="chain.empty", title="Chain is empty",
            detail="No audit entries exist yet in this environment.",
            status=DemoEventStatus.PASSED,
        )
        return DemoResult(
            scenario=VERIFY_CHAIN_RUN_KIND, detected_fault=None, terminal_outcome="CHAIN EMPTY",
            recovery_action="none -- nothing to verify yet", reserved_balance_minor=0,
            mandate_id="", trace_id="",
        )

    to_seq = tail[0]
    recorder.emit(
        phase="verify", kind="chain.scan_started", title="Chain scan started",
        detail=f"Recomputing every hash from seq 1 to {to_seq}.",
        status=DemoEventStatus.RUNNING,
    )
    async with UnitOfWork(session_factory) as uow:
        result = await verify_chain(uow, 1, to_seq)

    recorder.emit(
        phase="verify", kind="chain.entries_verified",
        title=f"{result.entries_verified} entries independently recomputed",
        detail="Every entry_hash/prev_hash link recomputed from scratch and compared.",
        status=DemoEventStatus.PASSED if result.ok else DemoEventStatus.FAILED,
        evidence=DemoEvidence(audit_seq=result.to_seq),
    )

    if result.checkpoints_matched:
        async with UnitOfWork(session_factory) as uow:
            checkpoints = await uow.audit_checkpoints.list_all()
        by_to_seq = {cp.to_seq: cp for cp in checkpoints}
        for matched_to_seq in result.checkpoints_matched:
            cp = by_to_seq.get(matched_to_seq)
            if cp is None:
                continue
            recorder.emit(
                phase="verify", kind="checkpoint.merkle_matched",
                title=f"Checkpoint seq {cp.from_seq}-{cp.to_seq} Merkle root matches",
                detail=_anchor_detail(cp.anchor_status),
                status=(
                    DemoEventStatus.PASSED if cp.anchor_status != "conflict"
                    else DemoEventStatus.FAILED
                ),
                evidence=DemoEvidence(checkpoint_status=cp.anchor_status),
            )
    else:
        recorder.emit(
            phase="verify", kind="checkpoint.none_yet",
            title="No checkpoint covers this range yet",
            detail="Checkpoints are written every N entries -- this segment hasn't reached one.",
            status=DemoEventStatus.PASSED,
        )

    if result.ok:
        terminal = "CHAIN VALID"
    else:
        broken_seq = result.break_.seq if result.break_ else "?"
        terminal = f"CHAIN BROKEN at seq {broken_seq}"
    recorder.emit(
        phase="verify", kind="chain.terminal", title=terminal,
        detail="Independent, read-only re-verification -- never trips the integrity halt.",
        status=DemoEventStatus.PASSED if result.ok else DemoEventStatus.FAILED,
    )

    return DemoResult(
        scenario=VERIFY_CHAIN_RUN_KIND,
        detected_fault=None if result.ok else "CHAIN_BREAK",
        terminal_outcome=terminal,
        recovery_action="none -- read-only verification",
        reserved_balance_minor=0,
        mandate_id="",
        trace_id="",
        seq_range=(1, to_seq),
        chain=result,
    )


def _result_json(result: DemoResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "scenario": result.scenario,
        "detected_fault": result.detected_fault,
        "terminal_outcome": result.terminal_outcome,
        "recovery_action": result.recovery_action,
        "reserved_balance_minor": result.reserved_balance_minor,
        "mandate_id": result.mandate_id or None,
        "trace_id": result.trace_id or None,
        "order_id": result.order_id,
        "seq_range": None if result.seq_range is None else list(result.seq_range),
        "chain_verified": None if result.chain is None else result.chain.ok,
        "entries_verified": None if result.chain is None else result.chain.entries_verified,
    }


def _event_json(event: DemoEvent) -> dict[str, Any]:
    evidence = {k: v for k, v in event.evidence.__dict__.items() if v is not None}
    return {
        "seq": event.seq,
        "ts": event.ts.isoformat(),
        "phase": event.phase,
        "kind": event.kind,
        "title": event.title,
        "detail": event.detail,
        "status": event.status.value,
        "evidence": evidence,
    }


def demo_run_json(run: DemoRun) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "scenario": run.scenario,
        "status": run.status.value,
        "started_at": run.started_at.isoformat(),
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "events": [_event_json(e) for e in run.events],
        "result": _result_json(run.result),
        "order_id": run.order_id,
        "error": run.error,
    }
