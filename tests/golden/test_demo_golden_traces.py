"""§28 P9 instruction 5 / production-readiness correction: golden traces
for all six §20.1 demo items -- the five named `--scenario` values plus
`verify_chain`, the sixth, closing `actl verify-chain` command,
formalised as a full registered member of `DEMO_ITEMS` with the same
golden-trace parity as the five scenarios (docs/adr/0010 decision 20).

Runs each scenario once, in its documented §20.1 order, against this
file's own fresh, isolated Postgres (tests/golden/conftest.py) -- audit
seq numbers start at 1, so `application.demo.run_scenario`'s deterministic
IDs (seeded per scenario name) and fixed `FrozenClock` make the exported
trace byte-identical to the committed fixture on every clean run
(`scripts/generate_demo_golden_traces.py` generated the fixtures the same
way). Then exports the whole assembled chain (all five scenarios,
seq 1..N) as `verify_chain`'s own trace. Compares byte-for-byte and
independently re-verifies each committed fixture's own hash chain
offline, using the same pure functions `tests/golden/test_golden_trace.py`
(§28 P3) and `scripts/export_audit_bundle.py`'s generated `verify_bundle.
py` both already use -- no database needed for that half.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application.demo import (
    DEMO_ITEMS,
    SCENARIOS,
    export_chain_trace,
    export_scenario_trace,
    run_scenario,
    verify_trace_offline,
)

FIXTURE_DIR = Path(__file__).parent.parent.parent / "fixtures" / "golden_traces"


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def generated_traces(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, dict[str, object]]:
    """§20.1's own documented order -- also the order `scripts/
    generate_demo_golden_traces.py` ran them in to produce the committed
    fixtures, so seq numbers line up identically here. `verify_chain` is
    exported last, once every scenario has run, spanning the whole chain
    they collectively wrote."""
    traces: dict[str, dict[str, object]] = {}
    for scenario in SCENARIOS:
        result = await run_scenario(scenario, session_factory)
        traces[scenario] = await export_scenario_trace(session_factory, result)
    traces["verify_chain"] = await export_chain_trace(session_factory)
    return traces


def _load_fixture(item: str) -> dict[str, object]:
    path = FIXTURE_DIR / f"demo_{item}.json"
    fixture: dict[str, object] = json.loads(path.read_text())
    return fixture


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("item", DEMO_ITEMS)
async def test_demo_trace_matches_committed_golden_fixture(
    item: str, generated_traces: dict[str, dict[str, object]]
) -> None:
    assert generated_traces[item] == _load_fixture(item)


@pytest.mark.parametrize("item", DEMO_ITEMS)
def test_committed_golden_fixture_verifies_offline(item: str) -> None:
    """Independent re-verification of the *committed* fixture's own hash
    chain -- no database, no fixture from `generated_traces` -- via the
    shared `application.demo.verify_trace_offline` (also used by `scripts/
    run_demo_suite.py`, §28 P9 production-readiness correction)."""
    ok, reason = verify_trace_offline(_load_fixture(item))
    assert ok, f"{item}: {reason}"


def test_all_six_golden_fixture_files_are_present() -> None:
    """`make verify`'s own "fail if any is missing" requirement, proven
    at the test level too: every item in `DEMO_ITEMS` must have a
    committed fixture file, not just the ones that happen to exist."""
    missing = [item for item in DEMO_ITEMS if not (FIXTURE_DIR / f"demo_{item}.json").exists()]
    assert not missing, f"missing golden fixtures for: {missing}"
