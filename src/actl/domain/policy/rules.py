"""Twelve deterministic rules (§10.1), evaluated in fixed order. Every rule
always runs — no short-circuit — so a denial explains *everything* that was
wrong, not just the first thing.

`PurchaseIntent` is the proposed order already enriched with the
price-locked quote (§7 steps 8-9: quote.request -> quote -> order.propose):
by the time it reaches the policy engine the buyer-agent's claimed
quote/catalog data has already been folded in, which is why one flat model
is enough for every rule below to do its job without further lookups.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, StrictInt

from actl.domain.audit.canonical import jcs
from actl.domain.mandate.models import Mandate
from actl.domain.policy.reason_codes import ReasonCode


class PurchaseIntent(BaseModel):
    model_config = ConfigDict(frozen=True)

    currency: str
    category: str
    merchant: str
    unit_price_minor: StrictInt
    total_minor: StrictInt
    nights: int
    rooms: int
    refundable: bool
    quoted_total_minor: StrictInt
    current_total_minor: StrictInt
    catalog_version: int
    mandate_spec_hash: str
    intent_hash: str


class PolicyContext(BaseModel):
    """Everything time- or state-dependent, frozen at the call site — the
    caller (application layer) reads the real clock/ledger/id generator and
    passes plain values in, so `evaluate()` itself never does."""

    model_config = ConfigDict(frozen=True)

    now: datetime
    reserved_minor: StrictInt
    txn_count: int
    catalog_version: int
    decision_id: str
    decision_ttl_s: int


def compute_intent_hash(intent: PurchaseIntent) -> str:
    """Same pattern as mandate.hashing.compute_spec_hash: sha256(JCS(intent
    minus intent_hash)), prefixed. §11 G2 / rule 12 both bind a decision to
    one exact intent via this value."""
    payload = intent.model_dump(mode="json", exclude={"intent_hash"})
    digest = hashlib.sha256(jcs(payload).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


@dataclass(frozen=True)
class RuleOutcome:
    rule: str
    input: dict[str, Any]
    result: Literal["pass", "fail"]
    reason_code: ReasonCode = ReasonCode.OK


RuleFn = Callable[[Mandate, PurchaseIntent, PolicyContext], RuleOutcome]


def _outcome(
    rule: str, trace_input: dict[str, Any], ok: bool, fail_code: ReasonCode
) -> RuleOutcome:
    return RuleOutcome(
        rule=rule,
        input=trace_input,
        result="pass" if ok else "fail",
        reason_code=ReasonCode.OK if ok else fail_code,
    )


def currency_match(mandate: Mandate, intent: PurchaseIntent, ctx: PolicyContext) -> RuleOutcome:
    ok = intent.currency == mandate.bounds.currency
    return _outcome(
        "currency.match",
        {"mandate": mandate.bounds.currency, "intent": intent.currency},
        ok,
        ReasonCode.CURRENCY_MISMATCH,
    )


def category_allow(mandate: Mandate, intent: PurchaseIntent, ctx: PolicyContext) -> RuleOutcome:
    ok = intent.category in mandate.bounds.allowed_categories
    return _outcome(
        "category.allow",
        {"requested": intent.category, "allowed": mandate.bounds.allowed_categories},
        ok,
        ReasonCode.CATEGORY_NOT_ALLOWED,
    )


def merchant_block(mandate: Mandate, intent: PurchaseIntent, ctx: PolicyContext) -> RuleOutcome:
    ok = intent.merchant not in mandate.bounds.blocked_merchants
    return _outcome(
        "merchant.block",
        {"merchant": intent.merchant, "blocked": mandate.bounds.blocked_merchants},
        ok,
        ReasonCode.MERCHANT_BLOCKED,
    )


def temporal_window(mandate: Mandate, intent: PurchaseIntent, ctx: PolicyContext) -> RuleOutcome:
    trace_input = {
        "now": ctx.now.isoformat(),
        "not_before": mandate.temporal.not_before.isoformat(),
        "expires_at": mandate.temporal.expires_at.isoformat(),
    }
    if ctx.now < mandate.temporal.not_before:
        return _outcome("temporal.window", trace_input, False, ReasonCode.MANDATE_NOT_YET_VALID)
    if ctx.now >= mandate.temporal.expires_at:
        return _outcome("temporal.window", trace_input, False, ReasonCode.MANDATE_EXPIRED)
    return RuleOutcome(rule="temporal.window", input=trace_input, result="pass")


def cap_unit(mandate: Mandate, intent: PurchaseIntent, ctx: PolicyContext) -> RuleOutcome:
    ok = intent.unit_price_minor <= mandate.bounds.max_unit_minor
    return _outcome(
        "cap.unit",
        {"unit": intent.unit_price_minor, "limit": mandate.bounds.max_unit_minor},
        ok,
        ReasonCode.UNIT_CAP_EXCEEDED,
    )


def cap_total(mandate: Mandate, intent: PurchaseIntent, ctx: PolicyContext) -> RuleOutcome:
    ok = ctx.reserved_minor + intent.total_minor <= mandate.bounds.max_total_minor
    return _outcome(
        "cap.total",
        {
            "requested": intent.total_minor,
            "reserved": ctx.reserved_minor,
            "cap": mandate.bounds.max_total_minor,
        },
        ok,
        ReasonCode.BUDGET_EXCEEDED,
    )


def cap_count(mandate: Mandate, intent: PurchaseIntent, ctx: PolicyContext) -> RuleOutcome:
    ok = ctx.txn_count < mandate.bounds.max_transactions
    return _outcome(
        "cap.count",
        {"used": ctx.txn_count, "limit": mandate.bounds.max_transactions},
        ok,
        ReasonCode.TXN_LIMIT_EXCEEDED,
    )


def quantity_match(mandate: Mandate, intent: PurchaseIntent, ctx: PolicyContext) -> RuleOutcome:
    ok = intent.nights == mandate.intent.nights and intent.rooms == mandate.intent.rooms
    return _outcome(
        "quantity.match",
        {
            "nights": {"intent": intent.nights, "mandate": mandate.intent.nights},
            "rooms": {"intent": intent.rooms, "mandate": mandate.intent.rooms},
        },
        ok,
        ReasonCode.QUANTITY_MISMATCH,
    )


def policy_refundable(mandate: Mandate, intent: PurchaseIntent, ctx: PolicyContext) -> RuleOutcome:
    ok = (not mandate.bounds.require_refundable) or intent.refundable
    return _outcome(
        "policy.refundable",
        {"item_refundable": intent.refundable, "required": mandate.bounds.require_refundable},
        ok,
        ReasonCode.REFUND_POLICY_VIOLATION,
    )


def price_delta(mandate: Mandate, intent: PurchaseIntent, ctx: PolicyContext) -> RuleOutcome:
    if intent.quoted_total_minor == 0:
        delta_bps = 0 if intent.current_total_minor == 0 else 10_000
    else:
        delta_bps = (
            abs(intent.current_total_minor - intent.quoted_total_minor)
            * 10_000
            // intent.quoted_total_minor
        )
    ok = delta_bps <= mandate.bounds.max_price_delta_bps
    return _outcome(
        "price.delta",
        {
            "quoted": intent.quoted_total_minor,
            "current": intent.current_total_minor,
            "bps": delta_bps,
        },
        ok,
        ReasonCode.PRICE_DRIFT,
    )


def catalog_freshness(mandate: Mandate, intent: PurchaseIntent, ctx: PolicyContext) -> RuleOutcome:
    ok = intent.catalog_version == ctx.catalog_version
    return _outcome(
        "catalog.freshness",
        {"quote": intent.catalog_version, "ctx": ctx.catalog_version},
        ok,
        ReasonCode.STALE_PRICE,
    )


def integrity_binding(mandate: Mandate, intent: PurchaseIntent, ctx: PolicyContext) -> RuleOutcome:
    spec_hash_ok = intent.mandate_spec_hash == (mandate.spec_hash or "")
    intent_hash_ok = intent.intent_hash == compute_intent_hash(intent)
    ok = spec_hash_ok and intent_hash_ok
    return _outcome(
        "integrity.binding",
        {
            "mandate_spec_hash_match": spec_hash_ok,
            "intent_hash_match": intent_hash_ok,
        },
        ok,
        ReasonCode.INTENT_MISMATCH,
    )


RULES: tuple[RuleFn, ...] = (
    currency_match,
    category_allow,
    merchant_block,
    temporal_window,
    cap_unit,
    cap_total,
    cap_count,
    quantity_match,
    policy_refundable,
    price_delta,
    catalog_freshness,
    integrity_binding,
)
