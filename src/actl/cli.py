"""actl demo | verify-chain | seed | explain | replay | policy-check (§25).

Each subcommand lands with the phase that owns the logic it calls into (P1
policy-check, P3 verify-chain, P4 seed, P9 demo). This file is an interface
entrypoint, not domain code: it may read the clock and the filesystem,
neither of which the pure policy engine it calls into is allowed to do.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from actl.domain.mandate.models import Mandate
from actl.domain.policy.engine import evaluate
from actl.domain.policy.reason_codes import ReasonCode
from actl.domain.policy.rules import PolicyContext, PurchaseIntent
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="actl")
    parser.add_argument("--version", action="version", version="actl 0.1.0")
    subparsers = parser.add_subparsers(dest="command")

    policy_check = subparsers.add_parser(
        "policy-check", help="evaluate a mandate/intent pair through the policy engine"
    )
    policy_check.add_argument("mandate_path", type=Path)
    policy_check.add_argument("intent_path", type=Path)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "policy-check":
        sys.exit(_policy_check(args.mandate_path, args.intent_path))
    parser.print_help()


if __name__ == "__main__":
    main()
