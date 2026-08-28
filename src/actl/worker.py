"""Background process: outbox relay, saga tick, reconciliation poller, Merkle
checkpointer, anchor writer, DLQ drainer (§5.1). At-least-once by construction
once those handlers exist; every handler must be idempotent.
"""

from __future__ import annotations

import asyncio

from actl.config import settings
from actl.platform.logging import configure_logging, get_logger

configure_logging(level=settings.log_level, json_format=settings.log_format == "json")
logger = get_logger(__name__)

# ponytail: real loop bodies (outbox relay, saga tick, reconciliation poll,
# Merkle checkpoint, DLQ drain) land with the phases that own them (P2, P3,
# P6). Wiring an idle loop now avoids retrofitting the process shape later.


async def main() -> None:
    logger.info("worker.startup", app_env=settings.app_env)
    try:
        while True:
            await asyncio.sleep(5)
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("worker.shutdown")


if __name__ == "__main__":
    asyncio.run(main())
