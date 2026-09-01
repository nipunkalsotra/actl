"""§28 P12 Merchant Control Center: thin, typed, read-mostly REST routes for
the merchant dashboard. Every route delegates to an already-tested
application service or repository -- no gate, saga, payment, ledger,
policy, or anchor logic is reimplemented here. Demo Lab is the one
write-capable surface, and it is a thin, guarded wrapper around
`application.demo.run_scenario`/`verify_chain`, the exact same
orchestration the `actl demo`/`actl verify-chain` CLI commands already use.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application.audit_service import verify_chain
from actl.application.demo import DemoResult, UnknownScenario, run_scenario
from actl.application.explain_service import OrderNotFoundForExplain, explain_order
from actl.application.growth.metrics import ArmMetrics, compute_growth_metrics
from actl.config import settings
from actl.infrastructure.db.uow import UnitOfWork
from actl.interfaces.http.deps import get_redis, get_session_factory, get_uow
from actl.interfaces.http.routers.audit import render_explain_result
from actl.platform.ids import new_id

router = APIRouter(tags=["merchant"])

_MONAD_TESTNET_EXPLORER_TX_URL = "https://testnet.monadscan.com/tx/{tx_hash}"


@router.get("/merchant/v1/health")
async def get_health(
    uow: UnitOfWork = Depends(get_uow),
    redis_client: Redis = Depends(get_redis),
) -> dict[str, Any]:
    """Non-sensitive system health only -- mode *names*
    (`payment_provider`/`anchor_provider`), never a key, token, or URL with
    credentials in it."""
    database = "ok"
    audit_chain = "ok"
    try:
        tail = await uow.audit_log.get_tail()
        audit_chain = "ok" if tail is not None else "empty"
    except Exception:
        database = "error"
        audit_chain = "error"

    redis_status = "ok"
    try:
        await redis_client.ping()
    except Exception:
        redis_status = "error"

    return {
        "api": "ok",
        "database": database,
        "redis": redis_status,
        "audit_chain": audit_chain,
        "payment_mode": settings.payment_provider,
        "anchor_mode": settings.anchor_provider,
    }


@router.get("/merchant/v1/orders")
async def list_orders(
    limit: Annotated[int, Query(gt=0, le=200)] = 50,
    uow: UnitOfWork = Depends(get_uow),
) -> dict[str, Any]:
    """Order id / SKU / amount / status only -- no buyer name or other PII
    (§18.2's `orders` table never stores one)."""
    orders = await uow.orders.list_recent(limit)
    items: list[dict[str, Any]] = []
    for order in orders:
        quote = await uow.quotes.get(order.quote_id)
        items.append(
            {
                "order_id": order.id,
                "sku": quote.sku if quote else None,
                "amount_minor": order.amount_minor,
                "currency": order.currency,
                "status": order.status,
                "decline_reason": order.decline_reason,
                "created_at": order.created_at.isoformat() if order.created_at else None,
            }
        )
    return {"items": items}


@router.get("/merchant/v1/order/{order_id}/audit")
async def get_order_audit(order_id: str, uow: UnitOfWork = Depends(get_uow)) -> dict[str, Any]:
    """The same real evidence `/buyer/v1/audit/explain/{order_id}` already
    serves (`render_explain_result` -- one projection, reused, never a
    second implementation), plus one thing the Order Explorer's "Audit
    chain verified" step needs and the buyer route doesn't claim: an
    actual, freshly recomputed `verify_chain` over this order's own real
    seq span, not just "the hash fields are present.\""""
    try:
        result = await explain_order(uow, order_id)
    except OrderNotFoundForExplain:
        raise HTTPException(
            status_code=404, detail={"reason_code": "ORDER_NOT_FOUND", "order_id": order_id}
        ) from None

    seqs = [item.seq for item in result.timeline if item.seq is not None]
    chain_verified: bool | None = None
    if seqs:
        verification = await verify_chain(uow, min(seqs), max(seqs))
        chain_verified = verification.ok

    payload = render_explain_result(result)
    payload["chain_verified"] = chain_verified
    return payload


def _arm_json(arm: ArmMetrics) -> dict[str, Any]:
    return {
        "arm": arm.arm,
        "sessions": arm.sessions,
        "orders": arm.orders,
        "conversion_rate": arm.conversion_rate,
        "aov_minor": arm.aov_minor,
        "upsell_offered": arm.upsell_offered,
        "upsell_accepted": arm.upsell_accepted,
        "attach_rate": arm.attach_rate,
    }


@router.get("/merchant/v1/kpis")
async def get_kpis(uow: UnitOfWork = Depends(get_uow)) -> dict[str, Any]:
    """Same real `compute_growth_metrics` computation `/metrics/growth`
    already serves (§28 P8), plus the one KPI that service doesn't cover:
    a real count of policy decisions the Money Action Gate actually denied
    -- "protected offers blocked", never a fabricated number."""
    metrics = await compute_growth_metrics(uow)
    protected_offers_blocked = await uow.decisions.count_denied()
    return {
        "baseline": _arm_json(metrics.baseline),
        "upsell": _arm_json(metrics.upsell),
        "revenue_uplift": metrics.revenue_uplift,
        "protected_offers_blocked": protected_offers_blocked,
    }


@router.get("/merchant/v1/trust")
async def get_trust_summary(uow: UnitOfWork = Depends(get_uow)) -> dict[str, Any]:
    """Chain head + latest checkpoint/anchor state -- a summary, not a wall
    of hashes (the full per-entry hash chain stays behind
    `/buyer/v1/audit/explain/{order_id}`'s progressive-disclosure UI)."""
    tail = await uow.audit_log.get_tail()
    checkpoints = await uow.audit_checkpoints.list_all()
    latest = checkpoints[-1] if checkpoints else None

    latest_json: dict[str, Any] | None = None
    if latest is not None:
        explorer_url = (
            _MONAD_TESTNET_EXPLORER_TX_URL.format(tx_hash=latest.anchor_tx)
            if latest.anchor_status == "anchored" and latest.anchor_tx
            else None
        )
        latest_json = {
            "from_seq": latest.from_seq,
            "to_seq": latest.to_seq,
            "merkle_root": latest.merkle_root,
            "anchor_status": latest.anchor_status,
            "anchor_tx": latest.anchor_tx,
            "anchor_chain_id": latest.anchor_chain_id,
            "anchor_contract_address": latest.anchor_contract_address,
            "anchored_at": latest.anchored_at.isoformat() if latest.anchored_at else None,
            "explorer_url": explorer_url,
        }

    return {
        "chain_head_seq": tail[0] if tail else None,
        "chain_head_hash": tail[1] if tail else None,
        "checkpoint_count": len(checkpoints),
        "latest_checkpoint": latest_json,
        "anchor_provider": settings.anchor_provider,
    }


# ---------------------------------------------------------------------------
# Demo Lab -- guarded, judge-facing demonstration only.
# ---------------------------------------------------------------------------


_SAFE_DEMO_APP_ENVS = frozenset({"local", "ci"})


def _require_safe_demo_environment() -> None:
    """Reject rather than silently no-op or (worse) actually run against a
    real deployment: Demo Lab writes real deterministic-id rows into
    whatever database this process is pointed at, so it must never be
    reachable unless this process is *also* already configured
    local/simulator-safe -- the same discipline `PaymentProvider`
    (§28 P5) and `LLMClient` (§28 P8) fail-closed on for their own real
    vs. simulated/fallback split.

    `app_env`'s three documented values (.env: `local | ci | demo`) split
    into two ephemeral, developer/test-only environments -- a dev machine
    and a CI runner's throwaway testcontainers Postgres/Redis, both safe
    -- versus `demo`, a persistent, judge-facing deployment that is not
    ephemeral and stays excluded here."""
    if settings.app_env not in _SAFE_DEMO_APP_ENVS or settings.payment_provider != "simulator":
        raise HTTPException(
            status_code=403,
            detail=(
                "Demo Lab requires APP_ENV in {'local', 'ci'} and "
                "PAYMENT_PROVIDER=simulator; "
                f"this process is app_env={settings.app_env!r} "
                f"payment_provider={settings.payment_provider!r}."
            ),
        )


def _demo_result_json(result: DemoResult) -> dict[str, Any]:
    return {
        "scenario": result.scenario,
        "detected_fault": result.detected_fault,
        "terminal_outcome": result.terminal_outcome,
        "recovery_action": result.recovery_action,
        "reserved_balance_minor": result.reserved_balance_minor,
        "mandate_id": result.mandate_id,
        "trace_id": result.trace_id,
        "seq_range": None if result.seq_range is None else list(result.seq_range),
        "chain_verified": None if result.chain is None else result.chain.ok,
        "entries_verified": None if result.chain is None else result.chain.entries_verified,
    }


async def _run_demo_scenario(
    scenario: str, session_factory: async_sessionmaker[AsyncSession]
) -> dict[str, Any]:
    _require_safe_demo_environment()
    try:
        # A fresh run_id per click: application.demo's own deterministic-id
        # seed is otherwise fixed per scenario name, which is exactly right
        # for a byte-stable CLI/golden-trace run but would collide on a
        # second click of the same Demo Lab card against this same,
        # already-populated database.
        result = await run_scenario(scenario, session_factory, run_id=new_id("run"))
    except UnknownScenario as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _demo_result_json(result)


@router.post("/merchant/v1/demo/stale-price")
async def demo_stale_price(
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> dict[str, Any]:
    return await _run_demo_scenario("stale_price", session_factory)


@router.post("/merchant/v1/demo/payment-decline")
async def demo_payment_decline(
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> dict[str, Any]:
    return await _run_demo_scenario("declined", session_factory)


@router.post("/merchant/v1/demo/llm-unavailable")
async def demo_llm_unavailable(
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> dict[str, Any]:
    return await _run_demo_scenario("llm_down", session_factory)


@router.post("/merchant/v1/demo/verify-chain")
async def demo_verify_chain(uow: UnitOfWork = Depends(get_uow)) -> dict[str, Any]:
    """The real, non-halting verifier (application.audit_service.
    verify_chain) over the whole current chain -- never the halting
    variant `actl verify-chain` uses on a real integrity failure, since a
    Demo Lab click must never trip the process-wide money-action halt as a
    side effect of a judge clicking a button."""
    _require_safe_demo_environment()
    tail = await uow.audit_log.get_tail()
    if tail is None:
        return {"ok": True, "from_seq": None, "to_seq": None, "entries_verified": 0}
    result = await verify_chain(uow, 1, tail[0])
    return {
        "ok": result.ok,
        "from_seq": result.from_seq,
        "to_seq": result.to_seq,
        "entries_verified": result.entries_verified,
        "checkpoints_matched": result.checkpoints_matched,
        "head_entry_hash": result.head_entry_hash,
    }

