"""§20.1 The four-minute demo script: `actl demo --scenario <name>`.

Five named scenarios (`happy_path`, `over_cap`, `stale_price`, `declined`,
`llm_down`) -- §20.1 lists exactly those five `--scenario` invocations,
then closes with a sixth, unparameterised command, `actl verify-chain
--from 1 --to 80`, already wired as its own subcommand (see
`cli.py::_verify_chain`, docs/adr/0010-p9-failure-theatre-decisions.md
decision 6: "six demo scenarios" = five `--scenario` names plus that
closing verification step, not a literal sixth `--scenario` value).

Every scenario drives the real P4-P7 code path -- `create_quote`,
`handle_order_propose` (the real Money Action Gate + saga),
`saga.complete_purchase` -- against `SimulatorAdapter` only, exactly like
`application.growth.simulation` (§28 P8) and `tests/chaos/test_f{1,2,6}.py`
(§28 P9) already do; `llm_down` uses `NullLLMClient`, real production
infrastructure for `LLM_ENABLED=false`, never Groq. No scenario imports
`actl.infrastructure.providers.razorpay` or `actl.infrastructure.llm.
groq_client` (§21 contracts 3-5 forbid it outside the gate/factory
regardless).

IDs are seeded deterministically per scenario name (`platform.ids.
seed_deterministic_ids`) and the clock is a fixed `FrozenClock` -- §28 P9
instruction 5's "stable across reruns through fixed seed, fixed clock,
and deterministic IDs" golden-trace requirement. This assumes each
scenario runs once against a freshly migrated database (`make demo`'s
job, §28 P9-7) -- re-running the same scenario a second time against a
database that still has the first run's rows will collide on those same
deterministic ids, exactly like re-running any other seed script twice
without resetting state first.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.application.agents.merchant import handle_order_propose
from actl.application.audit_service import ChainVerificationResult, verify_chain
from actl.application.catalog_service import create_quote
from actl.application.conversation.extraction import extract_mandate_draft
from actl.application.conversation.ranking import rank_candidates
from actl.application.orchestrator import saga
from actl.application.recovery import propose_with_one_requote_on_stale_price
from actl.config import settings
from actl.domain.audit.chain import (
    compute_entry_hash,
    hex_prefixed,
    parse_hex_prefixed,
    payload_hash,
)
from actl.domain.catalog.models import (
    CatalogAttributes,
    CatalogItem,
    CatalogLocation,
    CatalogPolicy,
)
from actl.domain.ledger.model import account, net_balance
from actl.domain.mandate.draft import ClarificationNeeded
from actl.domain.mandate.hashing import compute_spec_hash
from actl.domain.mandate.models import (
    Delegate,
    Mandate,
    MandateBounds,
    MandateControls,
    MandateIntent,
    MandateSignature,
    MandateTemporal,
    Principal,
)
from actl.domain.mandate.signing import sign_spec_hash
from actl.domain.mandate.state_machine import MandateStatus
from actl.domain.policy.reason_codes import ReasonCode
from actl.domain.policy.rules import PurchaseIntent, compute_intent_hash
from actl.infrastructure.db.repositories.catalog import CatalogItemRecord
from actl.infrastructure.db.uow import UnitOfWork
from actl.infrastructure.llm.fallback import NullLLMClient
from actl.infrastructure.providers.simulator.adapter import Scenario, SimulatorAdapter
from actl.platform.breaker import CircuitBreaker
from actl.platform.clock import FrozenClock
from actl.platform.ids import new_id, reset_ids, seed_deterministic_ids

SCENARIOS: tuple[str, ...] = ("happy_path", "over_cap", "stale_price", "declined", "llm_down")

# §20.1's own text: "Recording those six commands is the backbone of the
# pitch video" -- five `actl demo --scenario <name>` invocations (exactly
# `SCENARIOS`, unchanged, still the only valid `--scenario` CLI values)
# plus a sixth, differently-shaped closing command, `actl verify-chain
# --from 1 --to 80`. `VERIFY_CHAIN_ITEM`/`DEMO_ITEMS` formalise that
# sixth command as a full member of the registered demo-item set --
# printed, golden-traced, and offline-verified with the same parity as
# the five named scenarios -- so `make demo`/`make verify` cover exactly
# the six items §20.1 documents, never five-plus-an-afterthought (§28 P9
# production-readiness correction, docs/adr/0010 decision 20).
VERIFY_CHAIN_ITEM = "verify_chain"
DEMO_ITEMS: tuple[str, ...] = (*SCENARIOS, VERIFY_CHAIN_ITEM)

_DEMO_NOW = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)


class UnknownScenario(ValueError):
    def __init__(self, name: str) -> None:
        super().__init__(f"unknown scenario {name!r} -- choices: {', '.join(SCENARIOS)}")
        self.name = name


@dataclass(frozen=True)
class DemoResult:
    scenario: str
    detected_fault: str | None
    terminal_outcome: str
    recovery_action: str
    reserved_balance_minor: int
    mandate_id: str
    trace_id: str
    seq_range: tuple[int, int] | None = None
    chain: ChainVerificationResult | None = None


def _build_mandate(*, max_total_minor: int, max_unit_minor: int, nights: int = 3) -> Mandate:
    """Reimplemented here, not imported from `tests/` -- application code
    cannot import test modules; same shape/signing path as
    `application.growth.simulation._build_mandate`'s own precedent."""
    draft = Mandate(
        mandate_id=new_id("mdt"),
        version=1,
        principal=Principal(type="human", id="usr_demo"),
        delegate=Delegate(type="agent", id="agt_demo", key_id="ed25519:demo"),
        intent=MandateIntent(
            category="travel.hotel", location="Goa, IN", check_in="2026-09-12",
            nights=nights, rooms=1,
        ),
        bounds=MandateBounds(
            currency="INR",
            max_total_minor=max_total_minor,
            max_unit_minor=max_unit_minor,
            max_transactions=1,
            allowed_categories=["travel.hotel"],
            blocked_merchants=[],
            require_refundable=True,
            max_price_delta_bps=1000,
        ),
        temporal=MandateTemporal(
            not_before=datetime(2026, 1, 1, tzinfo=UTC),
            expires_at=datetime(2027, 1, 1, tzinfo=UTC),
            quote_ttl_s=120,
        ),
        controls=MandateControls(human_confirm_required=True, revocable=True),
    )
    spec_hash = compute_spec_hash(draft)
    signature = MandateSignature(
        alg="HMAC-SHA256",
        key_id="mk_demo",
        value=sign_spec_hash(spec_hash, settings.mandate_signing_key.encode("utf-8")),
    )
    return draft.model_copy(update={"spec_hash": spec_hash, "signature": signature})


async def _seed_item(
    session_factory: async_sessionmaker[AsyncSession], *, sku: str, unit_price_minor: int
) -> None:
    async with UnitOfWork(session_factory) as uow:
        current_version = await uow.catalog.current_version()
        await uow.catalog.upsert_item(
            CatalogItemRecord(
                sku=sku,
                category="travel.hotel",
                merchant_id="mrc_demo",
                unit="night",
                unit_price_minor=unit_price_minor,
                available_units=5,
                location_city="Goa",
                location_country="IN",
                rating=4.4,
                sea_facing=True,
                breakfast_included=True,
                refundable=True,
                cancellation_window_h=48,
                instant_confirm=True,
                taxes_included=True,
                quote_required=True,
                version=current_version,
            )
        )
        await uow.commit()


async def _reserved_balance(
    session_factory: async_sessionmaker[AsyncSession], mandate_id: str
) -> int:
    async with UnitOfWork(session_factory) as uow:
        entries = await uow.ledger_entries.list_for_account(account(mandate_id, "reserved"))
    return net_balance([(e.direction, e.amount_minor) for e in entries])


async def _chain_status(
    session_factory: async_sessionmaker[AsyncSession], seq_range: tuple[int, int] | None
) -> ChainVerificationResult | None:
    """Read-only verification of just this scenario's own contiguous seq
    span (same `start_seq+1..tail` shape `tests/chaos/test_f{1,6,9,10}.py`
    use) -- deliberately the non-halting `verify_chain`, not `_and_halt_
    on_failure`: a per-scenario status line must never trip the process-
    global integrity halt as a side effect of printing a demo summary."""
    if seq_range is None:
        return None
    async with UnitOfWork(session_factory) as uow:
        return await verify_chain(uow, seq_range[0], seq_range[1])


@dataclass(frozen=True)
class _Purchase:
    order_id: str | None
    saga_id: str | None
    quote_total_minor: int
    settled: bool
    deny_reason: ReasonCode | None


async def _propose_and_settle(
    session_factory: async_sessionmaker[AsyncSession],
    provider: SimulatorAdapter,
    clock: FrozenClock,
    breaker: CircuitBreaker,
    *,
    mandate: Mandate,
    sku: str,
    nights: int,
    actor_id: str,
    trace_id: str,
) -> _Purchase:
    """The real, deterministic P4-P7 transaction -- identical shape to
    `application.growth.simulation._attempt_purchase`."""
    async with UnitOfWork(session_factory) as uow:
        quote = await create_quote(
            uow, clock, mandate_id=mandate.mandate_id, sku=sku, nights=nights, actor_id=actor_id
        )
        await uow.commit()
        item = await uow.catalog.get_item(sku)
    assert item is not None
    assert quote.quote_hash is not None

    intent_draft = PurchaseIntent(
        currency=mandate.bounds.currency,
        category=item.category,
        merchant=item.merchant_id,
        unit_price_minor=quote.unit_price_minor,
        total_minor=quote.total_minor,
        nights=quote.nights,
        rooms=mandate.intent.rooms,
        refundable=quote.refundable,
        quoted_total_minor=quote.total_minor,
        current_total_minor=item.unit_price_minor * quote.nights,
        catalog_version=quote.catalog_version,
        mandate_spec_hash=mandate.spec_hash or "",
        intent_hash="",
    )
    intent_hash = compute_intent_hash(intent_draft)

    outcome = await handle_order_propose(
        session_factory, provider, clock, breaker,
        quote_id=quote.quote_id, quote_hash=quote.quote_hash,
        mandate_id=mandate.mandate_id, mandate_spec_hash=mandate.spec_hash or "",
        intent_hash=intent_hash, trace_id=trace_id, actor_id=actor_id,
    )
    if outcome.body.get("decision") != "accept":
        reason = ReasonCode(str(outcome.body["reason_code"]))
        return _Purchase(None, None, quote.total_minor, False, reason)

    order_id = str(outcome.body["order_id"])
    saga_id = str(outcome.body["saga_id"])
    async with UnitOfWork(session_factory) as uow:
        order = await uow.orders.get(order_id)
        # §28 P12: every order this module creates is a guarded demo/CLI
        # scenario run, never a real customer purchase -- tagged so
        # merchant KPIs' organic gross sales and Live Orders' display
        # never present it as one (migration 0010).
        await uow.orders.set_source(order_id, "demo_lab")
        await uow.commit()
    assert order is not None and order.provider_order_id is not None
    payments = await provider.fetch_payments(order.provider_order_id)
    payment = payments[0]
    signature = provider.build_checkout_payload(order.provider_order_id, payment.id)
    result = await saga.complete_purchase(
        saga_id, session_factory, provider, clock, breaker,
        provider_order_id=order.provider_order_id, provider_payment_id=payment.id,
        provider_signature=signature, actor_id=actor_id,
    )
    return _Purchase(order_id, saga_id, quote.total_minor, result.status == "COMPLETED", None)


async def _happy_path(session_factory: async_sessionmaker[AsyncSession]) -> DemoResult:
    clock = FrozenClock(at=_DEMO_NOW)
    provider = SimulatorAdapter(clock=clock)
    breaker = CircuitBreaker(name="demo-happy_path", clock=clock)
    actor_id = "agt_demo_happy_path"
    trace_id = new_id("trc")
    sku = "HTL-DEMO-HAPPY"

    mandate = _build_mandate(max_total_minor=900000, max_unit_minor=300000)
    await _seed_item(session_factory, sku=sku, unit_price_minor=280000)
    async with UnitOfWork(session_factory) as uow:
        await uow.mandates.add(mandate, MandateStatus.LOCKED)
        await uow.commit()

    purchase = await _propose_and_settle(
        session_factory, provider, clock, breaker,
        mandate=mandate, sku=sku, nights=3, actor_id=actor_id, trace_id=trace_id,
    )
    return DemoResult(
        scenario="happy_path",
        detected_fault=None,
        terminal_outcome="CAPTURED" if purchase.settled else f"DENY {purchase.deny_reason}",
        recovery_action="none (no fault injected) -- 7 gates pass end to end",
        reserved_balance_minor=await _reserved_balance(session_factory, mandate.mandate_id),
        mandate_id=mandate.mandate_id,
        trace_id=trace_id,
    )


async def _over_cap(session_factory: async_sessionmaker[AsyncSession]) -> DemoResult:
    clock = FrozenClock(at=_DEMO_NOW)
    provider = SimulatorAdapter(clock=clock)
    breaker = CircuitBreaker(name="demo-over_cap", clock=clock)
    actor_id = "agt_demo_over_cap"
    trace_id = new_id("trc")
    sku = "HTL-DEMO-OVERCAP"

    # §20.1's own worked example: "requested 500000 against max_unit_minor
    # 300000" -- one night at exactly that unit price keeps the total
    # (500000) safely under max_total_minor too, so cap.unit is the only
    # rule that fails (never a second, redundant cap.total denial).
    mandate = _build_mandate(max_total_minor=900000, max_unit_minor=300000, nights=1)
    await _seed_item(session_factory, sku=sku, unit_price_minor=500000)
    async with UnitOfWork(session_factory) as uow:
        await uow.mandates.add(mandate, MandateStatus.LOCKED)
        await uow.commit()

    purchase = await _propose_and_settle(
        session_factory, provider, clock, breaker,
        mandate=mandate, sku=sku, nights=1, actor_id=actor_id, trace_id=trace_id,
    )
    assert purchase.deny_reason == ReasonCode.UNIT_CAP_EXCEEDED, purchase
    return DemoResult(
        scenario="over_cap",
        detected_fault=str(ReasonCode.UNIT_CAP_EXCEEDED),
        terminal_outcome="DENY (rejected by the policy engine, before G4)",
        recovery_action="none needed -- no reservation taken, no provider call made",
        reserved_balance_minor=await _reserved_balance(session_factory, mandate.mandate_id),
        mandate_id=mandate.mandate_id,
        trace_id=trace_id,
    )


async def _stale_price(session_factory: async_sessionmaker[AsyncSession]) -> DemoResult:
    clock = FrozenClock(at=_DEMO_NOW)
    provider = SimulatorAdapter(clock=clock)
    breaker = CircuitBreaker(name="demo-stale_price", clock=clock)
    actor_id = "agt_demo_stale_price"
    trace_id = new_id("trc")
    sku = "HTL-DEMO-STALE"

    mandate = _build_mandate(max_total_minor=900000, max_unit_minor=300000)
    await _seed_item(session_factory, sku=sku, unit_price_minor=280000)
    async with UnitOfWork(session_factory) as uow:
        await uow.mandates.add(mandate, MandateStatus.LOCKED)
        pinned_quote = await create_quote(
            uow, clock, mandate_id=mandate.mandate_id, sku=sku, nights=3, actor_id=actor_id
        )
        await uow.commit()

    # --- FAULT INJECTION: the required out-of-band price mutation, never
    # the normal catalog-admin endpoint (§28 P9 instruction 1). ---
    async with UnitOfWork(session_factory) as uow:
        await uow.catalog.mutate_price(sku, 292000)
        await uow.commit()

    outcome = await propose_with_one_requote_on_stale_price(
        session_factory, provider, clock, breaker,
        mandate=mandate, quote=pinned_quote, actor_id=actor_id, trace_id=trace_id,
    )
    if (
        outcome.result.verdict == "ALLOW"
        and outcome.saga_id is not None
        and outcome.result.order_id
    ):
        async with UnitOfWork(session_factory) as uow:
            order = await uow.orders.get(outcome.result.order_id)
            # §28 P12: same tagging as _propose_and_settle -- this scenario's
            # own recovery/checkout path bypasses that helper, so it needs
            # its own tag rather than inheriting one.
            await uow.orders.set_source(outcome.result.order_id, "demo_lab")
            await uow.commit()
        assert order is not None and order.provider_order_id is not None
        payments = await provider.fetch_payments(order.provider_order_id)
        payment = payments[0]
        signature = provider.build_checkout_payload(order.provider_order_id, payment.id)
        await saga.complete_purchase(
            outcome.saga_id, session_factory, provider, clock, breaker,
            provider_order_id=order.provider_order_id, provider_payment_id=payment.id,
            provider_signature=signature, actor_id=actor_id,
        )

    return DemoResult(
        scenario="stale_price",
        detected_fault=f"{ReasonCode.STALE_PRICE} (catalog_version mismatch)",
        terminal_outcome=(
            "CAPTURED" if outcome.result.verdict == "ALLOW" else str(outcome.result.reason_code)
        ),
        recovery_action=(
            f"auto re-quote once -> re-evaluated at {outcome.final_quote.total_minor} -> "
            f"{outcome.result.verdict}"
        ),
        reserved_balance_minor=await _reserved_balance(session_factory, mandate.mandate_id),
        mandate_id=mandate.mandate_id,
        trace_id=trace_id,
    )


async def _declined(session_factory: async_sessionmaker[AsyncSession]) -> DemoResult:
    clock = FrozenClock(at=_DEMO_NOW)
    provider = SimulatorAdapter(clock=clock, scenario=Scenario.DECLINE)
    breaker = CircuitBreaker(name="demo-declined", clock=clock)
    actor_id = "agt_demo_declined"
    trace_id = new_id("trc")
    sku = "HTL-DEMO-DECLINED"

    mandate = _build_mandate(max_total_minor=900000, max_unit_minor=300000)
    await _seed_item(session_factory, sku=sku, unit_price_minor=280000)
    async with UnitOfWork(session_factory) as uow:
        await uow.mandates.add(mandate, MandateStatus.LOCKED)
        await uow.commit()

    purchase = await _propose_and_settle(
        session_factory, provider, clock, breaker,
        mandate=mandate, sku=sku, nights=3, actor_id=actor_id, trace_id=trace_id,
    )
    assert not purchase.settled, "Scenario.DECLINE must never settle"
    async with UnitOfWork(session_factory) as uow:
        final = await uow.mandates.get(mandate.mandate_id)
    assert final is not None
    return DemoResult(
        scenario="declined",
        detected_fault=str(ReasonCode.PROVIDER_DECLINED),
        terminal_outcome=f"mandate -> {final[1].value}",
        recovery_action="compensations applied in reverse, reservation released, no blind retry",
        reserved_balance_minor=await _reserved_balance(session_factory, mandate.mandate_id),
        mandate_id=mandate.mandate_id,
        trace_id=trace_id,
    )


async def _llm_down(session_factory: async_sessionmaker[AsyncSession]) -> DemoResult:
    """§17 Figure 17.1's HARD BOUNDARY: "If every LLM call failed, the
    transaction still completes correctly." `NullLLMClient` is real
    production infrastructure for `LLM_ENABLED=false`, not a test
    double -- the same client `infrastructure.llm.factory.build_llm_client`
    returns whenever the setting is off."""
    clock = FrozenClock(at=_DEMO_NOW)
    provider = SimulatorAdapter(clock=clock)
    breaker = CircuitBreaker(name="demo-llm_down", clock=clock)
    actor_id = "agt_demo_llm_down"
    trace_id = new_id("trc")
    sku = "HTL-DEMO-LLMDOWN"
    llm = NullLLMClient()

    extraction_result = await extract_mandate_draft(llm, "book me something nice in Goa")
    assert isinstance(extraction_result, ClarificationNeeded)

    mandate = _build_mandate(max_total_minor=900000, max_unit_minor=300000)
    await _seed_item(session_factory, sku=sku, unit_price_minor=280000)
    async with UnitOfWork(session_factory) as uow:
        await uow.mandates.add(mandate, MandateStatus.LOCKED)
        item = await uow.catalog.get_item(sku)
        await uow.commit()
    assert item is not None
    candidates = [
        CatalogItem(
            sku=item.sku, category=item.category, merchant_id=item.merchant_id, unit=item.unit,
            unit_price_minor=item.unit_price_minor, available_units=item.available_units,
            location=CatalogLocation(city=item.location_city, country=item.location_country),
            attributes=CatalogAttributes(
                rating=item.rating, sea_facing=item.sea_facing,
                breakfast_included=item.breakfast_included,
            ),
            policy=CatalogPolicy(
                refundable=item.refundable, cancellation_window_h=item.cancellation_window_h,
                instant_confirm=item.instant_confirm, taxes_included=item.taxes_included,
            ),
            version=item.version, quote_required=item.quote_required,
        )
    ]
    ranking_result = await rank_candidates(llm, candidates, mandate)
    assert ranking_result.degraded is True

    purchase = await _propose_and_settle(
        session_factory, provider, clock, breaker,
        mandate=mandate, sku=sku, nights=3, actor_id=actor_id, trace_id=trace_id,
    )
    return DemoResult(
        scenario="llm_down",
        detected_fault="LLM_UNAVAILABLE (every U1/U2 call)",
        terminal_outcome="CAPTURED" if purchase.settled else f"DENY {purchase.deny_reason}",
        recovery_action="deterministic fallback path used for extraction+ranking, degraded=true",
        reserved_balance_minor=await _reserved_balance(session_factory, mandate.mandate_id),
        mandate_id=mandate.mandate_id,
        trace_id=trace_id,
    )


_RUNNERS = {
    "happy_path": _happy_path,
    "over_cap": _over_cap,
    "stale_price": _stale_price,
    "declined": _declined,
    "llm_down": _llm_down,
}


async def run_scenario(
    scenario: str,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    run_id: str | None = None,
) -> DemoResult:
    """The `seq_range` reported (and verified) here is the scenario's own
    contiguous span of the chain -- `start_seq+1..end_seq`, captured around
    the whole call, the same shape `tests/chaos/test_f{1,6,9,10}.py` use
    for a single test's own segment of a shared chain. This is *wider*
    than "every entry written under the propose-time trace_id": `saga.
    complete_purchase` mints its own fresh trace_id for settlement/
    compensation evidence (§22's correlation model, matching a real,
    separate checkout callback), so scoping by trace_id alone would miss
    exactly the settlement/compensation entries a demo scenario exists to
    show.

    `run_id` is optional and additive: omitted (the CLI/golden-trace path,
    unchanged), the seed is exactly `actl-demo:<scenario>` as before --
    byte-identical ids every run, which is the whole point for a committed
    golden fixture. A caller that instead needs to run the *same* scenario
    repeatedly against a long-lived, already-populated database (§28 P12's
    merchant Demo Lab, where a judge may click "Stale price" more than
    once) supplies a fresh `run_id` per invocation so each run gets its own
    deterministic-but-unique id sequence instead of colliding on the
    previous run's now-persisted rows."""
    if scenario not in _RUNNERS:
        raise UnknownScenario(scenario)
    seed = f"actl-demo:{scenario}" if run_id is None else f"actl-demo:{scenario}:{run_id}"
    seed_deterministic_ids(seed)
    try:
        async with UnitOfWork(session_factory) as uow:
            start_tail = await uow.audit_log.get_tail()
        start_seq = start_tail[0] if start_tail is not None else 0

        result = await _RUNNERS[scenario](session_factory)

        async with UnitOfWork(session_factory) as uow:
            end_tail = await uow.audit_log.get_tail()
        end_seq = end_tail[0] if end_tail is not None else start_seq
        seq_range = (start_seq + 1, end_seq) if end_seq > start_seq else None
        chain = await _chain_status(session_factory, seq_range)
        return replace(result, seq_range=seq_range, chain=chain)
    finally:
        reset_ids()


async def _export_entries(
    session_factory: async_sessionmaker[AsyncSession], seq_range: tuple[int, int] | None
) -> list[dict[str, object]]:
    """Shared by `export_scenario_trace` and `export_chain_trace`. `ts` is
    deliberately excluded from each entry: it is a Postgres `DEFAULT
    now()` insert-time value, never derived from the injected
    `FrozenClock`, so it is real wall-clock time and can never be
    byte-stable across two separate runs -- same reasoning `tests/golden/
    test_golden_trace.py` (§28 P3) already applies by never touching `ts`
    at all in its own hash-chain fixture."""
    if seq_range is None:
        return []
    async with UnitOfWork(session_factory) as uow:
        rows = await uow.audit_log.list_range(*seq_range)
    return [
        {
            "seq": e.seq,
            "trace_id": e.trace_id,
            "actor_type": e.actor_type,
            "actor_id": e.actor_id,
            "action": str(e.action),
            "subject": e.subject,
            "payload": e.payload,
            "payload_hash": e.payload_hash,
            "prev_hash": e.prev_hash,
            "entry_hash": e.entry_hash,
        }
        for e in rows
    ]


async def export_scenario_trace(
    session_factory: async_sessionmaker[AsyncSession], result: DemoResult
) -> dict[str, object]:
    """Canonical, JSON-able evidence snapshot for one scenario -- §20's
    event/audit/decision/ledger evidence (the decision verdict and reason
    codes are already embedded in each `order.proposed` audit payload, so
    no separate decisions-table export is needed). Used for golden-trace
    generation and comparison (§28 P9 instruction 5)."""
    entries = await _export_entries(session_factory, result.seq_range)
    return {
        "scenario": result.scenario,
        "detected_fault": result.detected_fault,
        "terminal_outcome": result.terminal_outcome,
        "recovery_action": result.recovery_action,
        "reserved_balance_minor": result.reserved_balance_minor,
        "mandate_id": result.mandate_id,
        "trace_id": result.trace_id,
        "entries": entries,
    }


async def export_chain_trace(
    session_factory: async_sessionmaker[AsyncSession], *, from_seq: int = 1
) -> dict[str, object]:
    """The sixth, closing §20.1 item's own canonical trace -- `actl
    verify-chain --from 1 --to 80`, formalised as a full member of
    `DEMO_ITEMS` with the same golden-trace parity as the five named
    scenarios (§28 P9 production-readiness correction, docs/adr/0010
    decision 20). Unlike a single scenario's own segment, this spans the
    *entire* chain from `from_seq` to the current tail -- exactly what
    `actl verify-chain --from 1 --to <head>` checks -- since the six
    §20.1 commands are meant to run once, in strict order, against one
    freshly migrated database, so `from_seq=1..tail` after all five
    scenarios have run is exactly their five segments concatenated."""
    async with UnitOfWork(session_factory) as uow:
        tail = await uow.audit_log.get_tail()
    to_seq = tail[0] if tail is not None else from_seq - 1
    head_entry_hash = tail[1] if tail is not None else None
    seq_range = (from_seq, to_seq) if to_seq >= from_seq else None
    entries = await _export_entries(session_factory, seq_range)
    return {
        "scenario": VERIFY_CHAIN_ITEM,
        "detected_fault": None,
        "terminal_outcome": "CHAIN VALID" if head_entry_hash is not None else "CHAIN EMPTY",
        "recovery_action": "none -- read-only verification of the five scenarios' combined chain",
        "reserved_balance_minor": 0,
        "mandate_id": None,
        "trace_id": None,
        "from_seq": from_seq,
        "to_seq": to_seq,
        "head_entry_hash": head_entry_hash,
        "entries": entries,
    }


def verify_trace_offline(trace: dict[str, object]) -> tuple[bool, str | None]:
    """Pure, no-I/O, independent re-verification of one `export_scenario_
    trace` (or committed golden fixture)'s own hash chain -- no database.
    Shared by `tests/golden/test_demo_golden_traces.py` and `scripts/
    run_demo_suite.py` (§28 P9 production-readiness correction) so the
    two don't duplicate this check. Uses the same pure `domain.audit.
    chain` primitives `scripts/export_audit_bundle.py`'s generated
    `verify_bundle.py` and `tests/golden/test_golden_trace.py` (§28 P3)
    both already use. A trace is a mid-chain segment (seq does not start
    at 1), so -- exactly like `verify_bundle.py`'s own documented
    "partial range" behaviour -- the first entry's own claimed
    `prev_hash` is trusted as the segment's starting point rather than
    re-derived from genesis; every entry after that is independently
    recomputed and must chain to it. Returns `(ok, failure_reason)`."""
    entries = trace.get("entries")
    if not isinstance(entries, list) or not entries:
        return True, None

    prev_hash = parse_hex_prefixed(str(entries[0]["prev_hash"]))
    for i, entry in enumerate(entries):
        payload = entry["payload"]
        recomputed_payload_hash = hex_prefixed(payload_hash(payload))
        if recomputed_payload_hash != entry["payload_hash"]:
            return False, f"entry {i}: payload_hash mismatch"
        if hex_prefixed(prev_hash) != entry["prev_hash"]:
            return False, f"entry {i}: prev_hash mismatch"
        entry_hash = compute_entry_hash(prev_hash, payload)
        if hex_prefixed(entry_hash) != entry["entry_hash"]:
            return False, f"entry {i}: entry_hash mismatch"
        prev_hash = entry_hash
    return True, None
