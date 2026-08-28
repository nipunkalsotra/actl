"""§28 P3 exit criteria: `python scripts/tamper.py --seq 43` — bypasses the
append-only trigger via a table-owner session (the same privilege level the
`actl` role has as the table's creator) to simulate direct storage-layer
tampering, the scenario `actl verify-chain` exists to detect. This is a
deliberate demo/test tool, never called from application code.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text

from actl.infrastructure.db.engine import get_engine


async def tamper(seq: int) -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_update"))
        try:
            result = await conn.execute(
                text(
                    "UPDATE audit_log SET payload = payload || '{\"tampered\": true}'::jsonb "
                    "WHERE seq = :seq"
                ),
                {"seq": seq},
            )
            if result.rowcount == 0:
                raise SystemExit(f"no audit_log row with seq={seq}")
        finally:
            await conn.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_update"))
    await engine.dispose()
    print(f"tampered with audit_log.payload at seq={seq}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate storage-layer audit_log tampering")
    parser.add_argument("--seq", type=int, required=True)
    args = parser.parse_args()
    asyncio.run(tamper(args.seq))


if __name__ == "__main__":
    main()
