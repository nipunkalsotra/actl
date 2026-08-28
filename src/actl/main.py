"""API entrypoint. Serves the human API, the agent protocol, the webhook sink
and the read-only audit API (§5.1). At P0 only the health surface exists."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import redis.asyncio as redis
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from actl.config import settings
from actl.interfaces.http.routers import admin, catalog, well_known
from actl.platform.logging import configure_logging, get_logger

configure_logging(level=settings.log_level, json_format=settings.log_format == "json")
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.engine = create_async_engine(settings.database_url, pool_size=settings.db_pool_size)
    app.state.redis_client = redis.from_url(settings.redis_url)
    logger.info("app.startup", app_env=settings.app_env)
    try:
        yield
    finally:
        await app.state.engine.dispose()
        await app.state.redis_client.aclose()
        logger.info("app.shutdown")


app = FastAPI(title="Agentic Commerce Trust Layer", lifespan=lifespan)
app.include_router(catalog.router)
app.include_router(admin.router)
app.include_router(well_known.router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness only. No dependency calls — a healthy process always answers."""
    return {"status": "ok"}


@app.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    """Readiness: can this process actually serve traffic right now."""
    db_status = "ok"
    migration: str | None = None
    try:
        async with request.app.state.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            row = result.first()
            migration = row[0] if row else None
    except Exception as exc:
        db_status = "error"
        logger.warning("readyz.db_check_failed", error=str(exc))

    redis_status = "ok"
    try:
        await request.app.state.redis_client.ping()
    except Exception as exc:
        redis_status = "error"
        logger.warning("readyz.redis_check_failed", error=str(exc))

    ready = db_status == "ok" and redis_status == "ok"
    body: dict[str, Any] = {
        "status": "ready" if ready else "degraded",
        "db": db_status,
        "redis": redis_status,
        "migration": migration,
    }
    return JSONResponse(content=body, status_code=200 if ready else 503)
