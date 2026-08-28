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

from actl.application.audit_service import ChainVerificationResult, verify_chain
from actl.application.payment_service import WebhookReceipt, process_webhook_delivery
from actl.application.ports import ProviderOrder
from actl.config import settings
from actl.domain.mandate.models import Mandate
from actl.domain.policy.engine import evaluate
from actl.domain.policy.reason_codes import ReasonCode
from actl.domain.policy.rules import PolicyContext, PurchaseIntent
from actl.infrastructure.db.uow import UnitOfWork
from actl.infrastructure.providers.factory import build_payment_provider
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
    async def _run() -> ChainVerificationResult:
        async with UnitOfWork() as uow:
            return await verify_chain(uow, from_seq, to_seq)

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
    parser.print_help()


if __name__ == "__main__":
    main()
