"""Standalone helper for tests/chaos/test_f10.py's cross-process proof --
NOT collected by pytest (no `test_` prefix, matching `_helpers.py`'s own
precedent). Run as a genuinely separate OS process (`subprocess.run`,
`sys.executable`) against the SAME Postgres the parent test already
tripped the durable integrity halt in -- a fresh Python interpreter, a
fresh SQLAlchemy engine, zero shared state with the parent pytest process
beyond the same `DATABASE_URL`. Proves the halt-check in `application.
gate.execute_money_action` is genuinely durable (a Postgres row every
process reads) rather than any process-local Python state -- the exact
class of bug the old `application.integrity.IntegrityHalt` in-memory
singleton had (docs/adr/0010 decision 16).

Usage: DATABASE_URL=... python _f10_second_process.py \
    <mandate_id> <decision_id> <quote_id> <intent_hash> <amount_minor>
Prints one JSON line to stdout: {"verdict": ..., "reason_code": ...}
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from actl.application.gate import MoneyActionRequest, execute_money_action
from actl.infrastructure.providers.simulator.adapter import SimulatorAdapter
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import SystemClock
from actl.platform.ids import new_id


async def main() -> None:
    mandate_id, decision_id, quote_id, intent_hash, amount_minor = sys.argv[1:6]
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        clock = SystemClock()
        provider = SimulatorAdapter(clock=clock)
        breaker = CircuitBreaker(name="f10-second-process", clock=clock)
        req = MoneyActionRequest(
            trace_id=new_id("trc"),
            mandate_id=mandate_id,
            decision_id=decision_id,
            quote_id=quote_id,
            intent_hash=intent_hash,
            amount_minor=int(amount_minor),
            currency="INR",
            attempt_no=1,
        )
        result = await execute_money_action(req, session_factory, provider, clock, breaker)
        print(json.dumps({"verdict": result.verdict, "reason_code": str(result.reason_code)}))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
