"""The policy engine (§10): a pure, total function with no I/O, no clock, no
randomness and no exceptions. All twelve rules always run — no
short-circuit — so a DENY carries the complete explanation, not just the
first failing rule.

`decision_id` and `decision_ttl_s` are frozen inputs on `ctx`, not generated
here: generating a decision_id would mean minting a fresh ULID (randomness)
inside a function whose own docstring promises none, and reading
DECISION_TTL_S would mean this "pure" function reaching into config. Both
stay the caller's job (§26 DESIGN RULE: only settings.py reads the
environment) — evaluate() only ever turns frozen inputs into a record.
"""

from __future__ import annotations

import hashlib
from typing import Literal

from actl.domain.audit.canonical import jcs
from actl.domain.mandate.models import Mandate
from actl.domain.policy.decision import DecisionRecord, RuleTraceEntry
from actl.domain.policy.reason_codes import ReasonCode
from actl.domain.policy.rules import RULES, PolicyContext, PurchaseIntent

ENGINE_VERSION = "policy/1.0.0"


def _inputs_digest(mandate: Mandate, intent: PurchaseIntent, ctx: PolicyContext) -> str:
    """"replay this decision from the digest alone" (§8.2): a hash over the
    complete, exact input tuple evaluate() was called with."""
    payload = {
        "mandate": mandate.model_dump(mode="json", by_alias=True),
        "intent": intent.model_dump(mode="json"),
        "ctx": ctx.model_dump(mode="json"),
    }
    digest = hashlib.sha256(jcs(payload).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def evaluate(mandate: Mandate, intent: PurchaseIntent, ctx: PolicyContext) -> DecisionRecord:
    """Pure. No I/O, no wall clock, no randomness, no exceptions escape.

    Two calls with equal (mandate, intent, ctx) MUST produce byte-identical
    rule_trace, verdict and inputs_digest (§10.3 test_deterministic).
    """
    trace: list[RuleTraceEntry] = []
    reason_codes: list[ReasonCode] = []
    for rule_fn in RULES:
        outcome = rule_fn(mandate, intent, ctx)
        trace.append(RuleTraceEntry(rule=outcome.rule, input=outcome.input, result=outcome.result))
        if outcome.result == "fail":
            reason_codes.append(outcome.reason_code)

    verdict: Literal["ALLOW", "DENY"] = "DENY" if reason_codes else "ALLOW"

    return DecisionRecord(
        schema="actl.decision/v1",
        decision_id=ctx.decision_id,
        engine_version=ENGINE_VERSION,
        mandate_id=mandate.mandate_id,
        mandate_spec_hash=mandate.spec_hash or "",
        intent_hash=intent.intent_hash,
        verdict=verdict,
        reason_codes=reason_codes or [ReasonCode.OK],
        rule_trace=trace,
        evaluated_at=ctx.now,
        ttl_s=ctx.decision_ttl_s,
        inputs_digest=_inputs_digest(mandate, intent, ctx),
    )
