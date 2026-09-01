"""API entrypoint. Serves the human API, the agent protocol, the webhook sink
and the read-only audit API (§5.1). At P0 only the health surface exists."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import redis.asyncio as redis
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from actl.config import settings
from actl.infrastructure.llm.factory import build_llm_client
from actl.infrastructure.providers.factory import build_payment_provider
from actl.interfaces.agent import routes as agent_routes
from actl.interfaces.http.routers import admin, audit, buyer, catalog, growth, merchant, well_known
from actl.interfaces.webhooks import razorpay as razorpay_webhooks
from actl.platform import metrics
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import SystemClock
from actl.platform.logging import configure_logging, get_logger

configure_logging(level=settings.log_level, json_format=settings.log_format == "json")
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.engine = create_async_engine(settings.database_url, pool_size=settings.db_pool_size)
    app.state.redis_client = redis.from_url(settings.redis_url)
    # §28 P5: built once here, outside `actl.interfaces`/`actl.application`
    # (the import-linter contract's forbidden source_modules for reaching
    # a concrete payment provider) and handed to routers via
    # request.app.state, same as the engine and redis client above.
    app.state.payment_provider = build_payment_provider(settings)
    # §28 P7: one breaker per process, reused across requests (matching
    # worker.py's own breaker lifetime) -- its consecutive-failure state
    # is meaningless if rebuilt fresh on every call.
    app.state.breaker = CircuitBreaker(name="razorpay", clock=SystemClock())
    # §28 P12: same composition-root pattern as payment_provider above --
    # built once here (outside the import-linter contract's forbidden
    # source_modules for reaching the groq SDK directly) and handed to
    # routers via request.app.state; falls back to NullLLMClient/
    # ReplayLLMClient per LLM_ENABLED/DEMO_REPLAY exactly like the CLI/worker.
    app.state.llm_breaker = CircuitBreaker(name="groq", clock=SystemClock())
    app.state.llm_client = build_llm_client(
        settings,
        redis_client=app.state.redis_client,
        breaker=app.state.llm_breaker,
        clock=SystemClock(),
    )
    logger.info("app.startup", app_env=settings.app_env)
    try:
        yield
    finally:
        await app.state.engine.dispose()
        await app.state.redis_client.aclose()
        aclose = getattr(app.state.payment_provider, "aclose", None)
        if aclose is not None:
            await aclose()
        logger.info("app.shutdown")


app = FastAPI(title="Agentic Commerce Trust Layer", lifespan=lifespan)
app.include_router(catalog.router)
app.include_router(admin.router)
app.include_router(audit.router)
app.include_router(growth.router)
app.include_router(well_known.router)
app.include_router(razorpay_webhooks.router)
app.include_router(agent_routes.router)
app.include_router(buyer.router)
app.include_router(merchant.router)

# §28 P12 buyer frontend: local Vite dev server only, cross-origin to this
# API. No wildcard, no regex -- an explicit, small allow-list of exactly
# the two equivalent local dev origins `web/vite.config.ts`'s own
# `strictPort: true` guarantees Vite will actually bind to (it fails to
# start rather than silently drifting to another port, which is what
# previously caused browser preflights to reach this middleware with an
# origin outside this list -- a 400 there is CORSMiddleware working
# correctly, not a bug to work around by widening the list).
# A real production frontend origin, when one exists, belongs here as an
# explicit addition to this same list -- never a wildcard/regex stand-in.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _red_metrics(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """§22 RED per endpoint. `route` is the path *template* (e.g.
    "/agent/v1/messages"), never the raw URL -- a path param would make
    this an unbounded-cardinality label (§28 P10 instruction 2)."""
    start = time.perf_counter()
    response = await call_next(request)
    duration_s = time.perf_counter() - start
    route = request.scope.get("route")
    route_path = route.path if route is not None else "unmatched"
    metrics.http_requests_total.labels(
        route=route_path, method=request.method, status=str(response.status_code)
    ).inc()
    metrics.http_request_duration_seconds.labels(route=route_path, method=request.method).observe(
        duration_s
    )
    return response


@app.get("/metrics")
async def metrics_endpoint() -> Response:
    return Response(content=metrics.render(), media_type="text/plain; version=0.0.4")


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
