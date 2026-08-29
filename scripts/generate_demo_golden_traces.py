"""§28 P9 instruction 5: regenerates fixtures/golden_traces/demo_<item>.json
for all six §20.1 items -- the five named scenarios plus `verify_chain`,
the sixth, closing `actl verify-chain` command, formalised as a full
registered member of `application.demo.DEMO_ITEMS` with the same golden-
trace parity as the five scenarios (§28 P9 production-readiness
correction, docs/adr/0010 decision 20).

Spins up its own throwaway, isolated Postgres via testcontainers (never the
docker-compose dev database, which already has unrelated rows and would
make audit seq numbers start somewhere other than 1) -- same isolation
precedent as tests/integration/conftest.py. Runs the five §20.1 scenarios
once, in their documented order, writes each one's canonical trace
(`application.demo.export_scenario_trace`) to its own fixture file, then
exports the *whole* assembled chain (all five scenarios, seq 1..N) as the
sixth item's own trace (`application.demo.export_chain_trace`) -- exactly
what `actl verify-chain --from 1 --to <head>` checks.

Only re-run this deliberately, after an intentional behaviour change --
`tests/golden/test_demo_golden_traces.py` is what should fail first and
tell you a rerun is warranted, matching `tests/golden/test_golden_trace.py`
(§28 P3)'s own established golden-fixture precedent.

`application.payment_service`'s own `payment.intent` audit payload echoes
`settings.payment_provider` verbatim (a config label, independent of
which concrete `PaymentProvider` object the caller actually passed in --
every scenario here always uses `SimulatorAdapter` regardless of this
setting). `PAYMENT_PROVIDER` must be `simulator` when this runs, or the
committed fixture bakes in whatever your shell's default happens to be
(`"razorpay"`, per `config.Settings`) and every later `make demo`/CI run
-- which always exports `PAYMENT_PROVIDER=simulator` -- disagrees with it.

Usage: LLM_ENABLED=false PAYMENT_PROVIDER=simulator \
  uv run python scripts/generate_demo_golden_traces.py
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

# Docker Desktop's socket isn't shared with the Ryuk cleanup sidecar in this
# environment; same workaround as tests/integration/conftest.py -- must be
# set before importing testcontainers.core.container.
os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from actl.application.demo import (
    SCENARIOS,
    export_chain_trace,
    export_scenario_trace,
    run_scenario,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "fixtures" / "golden_traces"


def _write(name: str, trace: dict[str, object]) -> None:
    out_path = OUT_DIR / f"demo_{name}.json"
    out_path.write_text(json.dumps(trace, indent=2, sort_keys=True) + "\n")
    entries = trace["entries"]
    assert isinstance(entries, list)
    print(f"wrote {out_path.relative_to(REPO_ROOT)} ({len(entries)} entries)")


async def _generate(postgres_url: str) -> None:
    engine = create_async_engine(postgres_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        for scenario in SCENARIOS:
            result = await run_scenario(scenario, session_factory)
            trace = await export_scenario_trace(session_factory, result)
            _write(scenario, trace)
        chain_trace = await export_chain_trace(session_factory)
        _write("verify_chain", chain_trace)
    finally:
        await engine.dispose()


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    container = PostgresContainer("postgres:16", driver="asyncpg")
    with container:
        url = container.get_connection_url()
        env = {**os.environ, "DATABASE_URL": url}
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=REPO_ROOT, env=env, check=True,
        )
        asyncio.run(_generate(url))


if __name__ == "__main__":
    main()
