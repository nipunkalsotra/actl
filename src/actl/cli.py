"""actl demo | verify-chain | seed | explain | replay | policy-check (§25).

Each subcommand lands with the phase that owns the logic it calls into (P1
policy-check, P3 verify-chain, P4 seed, P9 demo). This file is an interface
entrypoint, not domain code: it may read the clock and the filesystem,
neither of which the pure policy engine it calls into is allowed to do.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from actl.application import ledger_service
from actl.application.audit_service import ChainVerificationResult, verify_chain_and_halt_on_failure
from actl.application.demo import SCENARIOS, DemoResult, UnknownScenario, run_scenario
from actl.application.growth.events import ARM_UPSELL
from actl.application.growth.metrics import ArmMetrics, GrowthMetrics, compute_growth_metrics
from actl.application.growth.simulation import SessionOutcome, run_growth_simulation
from actl.application.integrity import IntegrityHalted
from actl.application.payment_service import WebhookReceipt, process_webhook_delivery
from actl.application.ports import ProviderOrder
from actl.config import settings
from actl.domain.mandate.models import Mandate
from actl.domain.policy.engine import evaluate
from actl.domain.policy.reason_codes import ReasonCode
from actl.domain.policy.rules import PolicyContext, PurchaseIntent
from actl.infrastructure.db.engine import get_session_factory
from actl.infrastructure.db.uow import UnitOfWork
from actl.infrastructure.providers.factory import build_payment_provider
from actl.infrastructure.providers.simulator.adapter import SimulatorAdapter
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import SystemClock
from actl.platform.ids import new_id


def _policy_check(mandate_path: Path, intent_path: Path) -> int:
    mandate = Mandate.model_validate_json(mandate_path.read_text())
    intent = PurchaseIntent.model_validate_json(intent_path.read_text())
    ctx = PolicyContext(
        now=SystemClock().now(),
        reserved_minor=0,
        txn_count=0,
        catalog_version=intent.catalog_version,
        decision_id=new_id("dec"),
        decision_ttl_s=30,
    )
    decision = evaluate(mandate, intent, ctx)

    codes = [c for c in decision.reason_codes if c != ReasonCode.OK]
    print(f"{decision.verdict} {' '.join(codes)}".rstrip())
    failed = [entry for entry in decision.rule_trace if entry.result == "fail"]
    for entry in failed:
        print(f"rule {entry.rule} {json.dumps(entry.input)} -> fail")
    total = len(decision.rule_trace)
    print(f"{total} rules evaluated, {len(failed)} failed, engine {decision.engine_version}")
    return 0 if decision.verdict == "ALLOW" else 1


def _chain_head() -> int:
    async def _run() -> tuple[int, str] | None:
        async with UnitOfWork() as uow:
            return await uow.audit_log.get_tail()

    tail = asyncio.run(_run())
    if tail is None:
        print("chain is empty")
        return 1
    seq, entry_hash = tail
    print(f"head={entry_hash} seq={seq}")
    return 0


def _verify_chain(from_seq: int, to_seq: int) -> int:
    """§20 F10: this is the "Verifier" -- a failure here durably trips the
    cross-process integrity halt (`integrity_halt`, docs/adr/0010 decision
    2), refusing every subsequent money action on every process reading
    this database, via the gate's own first check."""

    async def _run() -> ChainVerificationResult:
        async with UnitOfWork() as uow:
            result = await verify_chain_and_halt_on_failure(uow, from_seq, to_seq, SystemClock())
            await uow.commit()
            return result

    result = asyncio.run(_run())

    print(f"scanning {to_seq - from_seq + 1} entries")
    if not result.ok:
        b = result.break_
        assert b is not None
        print(f"CHAIN BROKEN at seq={b.seq}")
        print(f"  expected {b.expected_entry_hash}")
        print(f"  computed {b.computed_entry_hash}")
        print(f"  reason: {b.reason}")
        if result.entries_verified > 0:
            print(f"entries {from_seq}..{from_seq + result.entries_verified - 1} verified intact")
        return 1

    print(f"recomputed {result.entries_verified} payload hashes .................. ok")
    print(f"recomputed {result.entries_verified} entry hashes .................... ok")
    print(f"sequence gapless ({from_seq}..{to_seq}) ....................... ok")
    if result.checkpoints_matched:
        checkpoints_str = ",".join(str(seq) for seq in result.checkpoints_matched)
        print(f"merkle roots matched at checkpoints {checkpoints_str} ok")
    print(
        f"CHAIN VALID   head={result.head_entry_hash}   "
        f"entries={result.entries_verified}   checkpoints={len(result.checkpoints_matched)}"
    )
    return 0


def _provider_smoke(amount: int) -> int:
    """§28 P5 exit criteria: one real call against whichever provider
    `PAYMENT_PROVIDER` selects — `create_order` only, never capture (§28
    P5 instruction 8: no real payment/capture unless the architecture
    requires it, and it does not here)."""

    async def _run() -> ProviderOrder:
        provider = build_payment_provider(settings)
        try:
            key = f"ik_smoke_{new_id('x')[:24]}"
            return await provider.create_order(
                amount, "INR", key, notes={"purpose": "actl provider-smoke"}
            )
        finally:
            aclose = getattr(provider, "aclose", None)
            if aclose is not None:
                await aclose()

    order = asyncio.run(_run())
    mode = "test mode" if settings.payment_provider == "razorpay" else "simulator"
    print(
        f"created {order.id} amount={amount} currency={order.currency} "
        f"status={order.status} ({mode})"
    )
    return 0


def _replay_webhook(fixture_path: Path) -> int:
    """§28 P5 exit criteria. Fixture shape: {"event_id", "signature",
    "body"} — `body` re-serialised compactly and deterministically (the
    exact bytes the fixture's signature was computed over)."""
    fixture = json.loads(fixture_path.read_text())
    event_id: str = fixture["event_id"]
    signature: str = fixture["signature"]
    body: dict[str, object] = fixture["body"]
    raw_body = json.dumps(body, separators=(",", ":")).encode("utf-8")
    event_type = str(body.get("event", "unknown"))

    async def _run() -> WebhookReceipt:
        provider = build_payment_provider(settings)
        try:
            async with UnitOfWork() as uow:
                return await process_webhook_delivery(
                    uow,
                    provider,
                    raw_body=raw_body,
                    signature=signature,
                    event_id=event_id,
                    event_type=event_type,
                    payload=body,
                )
        finally:
            aclose = getattr(provider, "aclose", None)
            if aclose is not None:
                await aclose()

    receipt = asyncio.run(_run())
    if receipt.outcome == "invalid_signature":
        print(f"signature INVALID event_id={event_id} -> dropped, never processed")
        return 1
    if receipt.outcome == "accepted":
        outcome_str = "processed"
    else:
        outcome_str = "duplicate, absorbed (no state change)"
    print(f"signature ok event_id={event_id} -> {outcome_str}")
    return 0


def _format_arm(arm: ArmMetrics) -> str:
    aov = f"{arm.aov_minor / 100:,.2f}" if arm.aov_minor is not None else "n/a"
    attach = f"{arm.attach_rate:.1%}" if arm.attach_rate is not None else "n/a"
    return (
        f"arm={arm.arm:<10s} conv={arm.conversion_rate:.1%}  aov={aov:>10s}  "
        f"attach={attach:>6s}  n={arm.sessions}"
    )


def _growth(seed: str, sessions: int) -> int:
    """§28 P8 instruction 9: `actl growth --seed demo --sessions N`. Real
    P4-P7 deterministic flow, SimulatorAdapter only -- never Razorpay,
    never Groq (`application.growth.simulation` never imports either)."""

    async def _run() -> tuple[list[SessionOutcome], GrowthMetrics]:
        session_factory = get_session_factory()
        provider = SimulatorAdapter(clock=SystemClock())
        clock = SystemClock()
        breaker = CircuitBreaker(name="growth-simulator", clock=clock)
        outcomes = await run_growth_simulation(
            session_factory, provider, clock, breaker, seed=seed, sessions=sessions
        )
        async with UnitOfWork(session_factory) as uow:
            metrics = await compute_growth_metrics(uow)
        return outcomes, metrics

    outcomes, metrics = asyncio.run(_run())

    denied_upsells = sum(
        1
        for o in outcomes
        if o.arm == ARM_UPSELL and o.upsell_accepted and o.upsell_order_id is None
    )

    print(_format_arm(metrics.baseline))
    print(_format_arm(metrics.upsell))
    uplift = metrics.revenue_uplift
    uplift_str = f"{uplift:+.1%}" if uplift is not None else "n/a"
    print(
        f"revenue uplift {uplift_str}   "
        f"(bounds still enforced: {denied_upsells} upsells denied at G4)"
    )
    return 0


def _sweep(ttl_s: int) -> int:
    """§12.2 / §20 F8: force-releases HELD reservations older than `ttl_s`
    -- the operator-triggered recovery step docs/runbook.md's F8 section
    names. Not wired into `actl.worker`'s own background loops (§28 P8
    exit criteria only requires the webhook and reconciliation pollers
    there); this thin CLI wrapper around the existing, already-tested
    `application.ledger_service.sweep` is the operational surface for it.
    Refuses to run at all while the durable §20 F10 integrity halt is
    tripped (`ledger_service.sweep`'s own `raise_if_halted` check) --
    "scheduled/sweep entry points must ... refuse money-affecting work
    while the halt is active" (§28 P9 instruction 2)."""

    async def _run() -> list[str]:
        async with UnitOfWork() as uow:
            swept = await ledger_service.sweep(uow, SystemClock(), reservation_ttl_s=ttl_s)
            await uow.commit()
        return swept

    try:
        swept = asyncio.run(_run())
    except IntegrityHalted as exc:
        print(f"REFUSED: integrity halt active ({exc.reason}) -- see docs/runbook.md F10")
        return 1
    if not swept:
        print(f"no HELD reservations older than {ttl_s}s")
        return 0
    print(f"swept {len(swept)} reservation(s) older than {ttl_s}s:")
    for ref_id in swept:
        print(f"  {ref_id}")
    return 0


def _print_demo_result(result: DemoResult) -> None:
    print(f"scenario: {result.scenario}")
    print(f"detected fault: {result.detected_fault or 'none'}")
    print(f"terminal outcome: {result.terminal_outcome}")
    print(f"recovery/compensation: {result.recovery_action}")
    print(f"reserved balance: {result.reserved_balance_minor}")
    if result.chain is None:
        print("audit chain: no entries written under this trace_id")
    elif result.chain.ok:
        seq_from, seq_to = result.seq_range or (0, 0)
        print(
            f"audit chain: VALID  seq {seq_from}..{seq_to}  "
            f"head={result.chain.head_entry_hash}"
        )
    else:
        b = result.chain.break_
        assert b is not None
        print(f"audit chain: BROKEN at seq={b.seq}  reason={b.reason}")
    print(f"trace: {result.trace_id}  mandate: {result.mandate_id}")


def _demo(scenario: str) -> int:
    """§20.1 the four-minute demo script -- `actl demo --scenario <name>`.
    Never calls Razorpay or Groq (`application.demo` uses SimulatorAdapter
    and NullLLMClient only). argparse's own `choices=SCENARIOS` already
    rejects an invalid name with a useful listing before this runs;
    `UnknownScenario` is `run_scenario`'s own defense-in-depth guard."""

    async def _run() -> DemoResult:
        session_factory = get_session_factory()
        return await run_scenario(scenario, session_factory)

    try:
        result = asyncio.run(_run())
    except UnknownScenario as exc:
        print(str(exc))
        return 1
    _print_demo_result(result)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="actl")
    parser.add_argument("--version", action="version", version="actl 0.1.0")
    subparsers = parser.add_subparsers(dest="command")

    policy_check = subparsers.add_parser(
        "policy-check", help="evaluate a mandate/intent pair through the policy engine"
    )
    policy_check.add_argument("mandate_path", type=Path)
    policy_check.add_argument("intent_path", type=Path)

    verify_chain_parser = subparsers.add_parser(
        "verify-chain", help="recompute and validate the audit hash chain (§16.2)"
    )
    verify_chain_parser.add_argument("--from", dest="from_seq", type=int, required=True)
    verify_chain_parser.add_argument("--to", dest="to_seq", type=int, required=True)

    subparsers.add_parser("chain-head", help="print the current audit chain tail")

    provider_smoke_parser = subparsers.add_parser(
        "provider-smoke", help="create one order via the configured PaymentProvider (§28 P5)"
    )
    provider_smoke_parser.add_argument("--amount", type=int, required=True)

    replay_webhook_parser = subparsers.add_parser(
        "replay-webhook", help="replay a webhook fixture through the receiver (§28 P5)"
    )
    replay_webhook_parser.add_argument("fixture_path", type=Path)

    growth_parser = subparsers.add_parser(
        "growth", help="run N seeded upsell-on/off sessions and print both arms (§28 P8)"
    )
    growth_parser.add_argument("--seed", type=str, required=True)
    growth_parser.add_argument("--sessions", type=int, required=True)

    demo_parser = subparsers.add_parser(
        "demo", help="run one of the six §20.1 demo scenarios (§28 P9)"
    )
    demo_parser.add_argument("--scenario", type=str, required=True, choices=SCENARIOS)

    sweep_parser = subparsers.add_parser(
        "sweep", help="force-release HELD reservations older than --ttl-s (§12.2, §20 F8)"
    )
    sweep_parser.add_argument("--ttl-s", type=int, default=settings.reservation_ttl_s)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "policy-check":
        sys.exit(_policy_check(args.mandate_path, args.intent_path))
    if args.command == "verify-chain":
        sys.exit(_verify_chain(args.from_seq, args.to_seq))
    if args.command == "chain-head":
        sys.exit(_chain_head())
    if args.command == "provider-smoke":
        sys.exit(_provider_smoke(args.amount))
    if args.command == "replay-webhook":
        sys.exit(_replay_webhook(args.fixture_path))
    if args.command == "growth":
        sys.exit(_growth(args.seed, args.sessions))
    if args.command == "demo":
        sys.exit(_demo(args.scenario))
    if args.command == "sweep":
        sys.exit(_sweep(args.ttl_s))
    parser.print_help()


if __name__ == "__main__":
    main()
