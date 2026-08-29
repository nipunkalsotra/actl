"""§28 P9 production-readiness correction: `make demo`'s own explicit,
human-readable coverage summary for all six §20.1 demo items -- the five
named `--scenario` values plus `verify_chain`, the sixth, closing `actl
verify-chain` command, formalised as a full registered member of
`application.demo.DEMO_ITEMS` with the same golden-trace parity as the
five scenarios (docs/adr/0010 decision 20). Each of the six is printed
with its own committed golden-trace path and independently verified
offline.

Spins up its own throwaway, isolated Postgres via testcontainers (same
precedent as `scripts/generate_demo_golden_traces.py`) so this is always
safely re-runnable, never touching a live/dev database. Runs the five
scenarios in §20.1's own documented order, compares each against its
committed golden trace (byte-for-byte, `application.demo.
export_scenario_trace`) and independently re-verifies that trace's own
hash chain offline (`application.demo.verify_trace_offline`). Then
exports the whole assembled chain (all five scenarios, seq 1..N) as
`verify_chain`'s own trace (`application.demo.export_chain_trace`),
compares and offline-verifies it exactly like the other five, and -- as
an additional, independent check specific to this item -- also exports
that same range as a standalone audit bundle (`scripts/
export_audit_bundle.py`, the existing audit-chain/export verifier §28 P9
instruction 5 names) and verifies it with its own generated, dependency-
free `verify_bundle.py`, run as a genuinely separate process.

Usage: uv run python scripts/run_demo_suite.py
Exit code 0 if all six pass, 1 otherwise.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")

REPO_ROOT = Path(__file__).resolve().parents[1]
# `scripts/` has no __init__.py -- running this file directly puts only
# its own directory on sys.path[0], not the repo root, so a sibling-
# module import (`export_audit_bundle`) would otherwise fail with
# ModuleNotFoundError regardless of cwd.
sys.path.insert(0, str(REPO_ROOT))

from scripts.export_audit_bundle import export_bundle  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.community.postgres import PostgresContainer  # noqa: E402

from actl.application.demo import (  # noqa: E402
    DEMO_ITEMS,
    SCENARIOS,
    VERIFY_CHAIN_ITEM,
    export_chain_trace,
    export_scenario_trace,
    run_scenario,
    verify_trace_offline,
)

FIXTURE_DIR = REPO_ROOT / "fixtures" / "golden_traces"
BUNDLE_DIR = REPO_ROOT / "demo_suite_bundle"


def _row(name: str, ok: bool, path: str) -> str:
    status = "PASS" if ok else "FAIL"
    return f"{name:<14} {status:<4}  {path}"


async def _verify_bundle_independently(
    session_factory: async_sessionmaker[AsyncSession], to_seq: int
) -> tuple[bool, str]:
    """Additional, independent re-verification of `verify_chain`'s own
    range, exported as a standalone bundle and checked by its own
    generated, dependency-free `verify_bundle.py` -- a genuinely separate
    process, not just a second in-process call."""
    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    await export_bundle(BUNDLE_DIR, 1, to_seq, session_factory)
    proc = subprocess.run(
        [sys.executable, str(BUNDLE_DIR / "verify_bundle.py"), str(BUNDLE_DIR)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.returncode == 0, proc.stdout + proc.stderr


async def _run_suite(postgres_url: str) -> bool:
    engine = create_async_engine(postgres_url)
    all_ok = True
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        for scenario in SCENARIOS:
            result = await run_scenario(scenario, session_factory)
            trace = await export_scenario_trace(session_factory, result)
            golden_path = FIXTURE_DIR / f"demo_{scenario}.json"
            golden = json.loads(golden_path.read_text())
            matches = trace == golden
            offline_ok, offline_reason = verify_trace_offline(golden)
            ok = matches and offline_ok
            all_ok = all_ok and ok
            print(_row(scenario, ok, str(golden_path.relative_to(REPO_ROOT))))
            if not matches:
                print("  generated trace does not match the committed golden fixture")
            if not offline_ok:
                print(f"  offline verification failed: {offline_reason}")

        chain_trace = await export_chain_trace(session_factory)
        chain_golden_path = FIXTURE_DIR / f"demo_{VERIFY_CHAIN_ITEM}.json"
        chain_golden = json.loads(chain_golden_path.read_text())
        chain_matches = chain_trace == chain_golden
        chain_offline_ok, chain_offline_reason = verify_trace_offline(chain_golden)
        to_seq = chain_trace["to_seq"]
        assert isinstance(to_seq, int)
        bundle_ok, bundle_output = await _verify_bundle_independently(session_factory, to_seq)
        chain_ok = chain_matches and chain_offline_ok and bundle_ok
        all_ok = all_ok and chain_ok
        print(_row(VERIFY_CHAIN_ITEM, chain_ok, str(chain_golden_path.relative_to(REPO_ROOT))))
        if not chain_matches:
            print("  generated chain trace does not match the committed golden fixture")
        if not chain_offline_ok:
            print(f"  offline verification failed: {chain_offline_reason}")
        if not bundle_ok:
            print(f"  independent bundle re-verification failed:\n{bundle_output}")
    finally:
        await engine.dispose()
    return all_ok


def main() -> None:
    container = PostgresContainer("postgres:16", driver="asyncpg")
    with container:
        url = container.get_connection_url()
        env = {**os.environ, "DATABASE_URL": url}
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=REPO_ROOT, env=env, check=True,
        )
        all_ok = asyncio.run(_run_suite(url))

    print()
    assert len(DEMO_ITEMS) == 6
    if all_ok:
        print("6 scenarios completed")
        sys.exit(0)
    else:
        print("one or more scenarios FAILED -- see above")
        sys.exit(1)


if __name__ == "__main__":
    main()
