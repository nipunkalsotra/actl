"""§10.3 — the real proof. Four property tests from the architecture doc,
implemented essentially verbatim against this repo's actual field names."""

from hypothesis import given

from actl.domain.audit.canonical import jcs
from actl.domain.policy.engine import evaluate

from .strategies import contexts, ctx_zero, intents, mandates


@given(mandate=mandates(), intent=intents())
def test_never_allows_above_total_cap(mandate, intent) -> None:
    d = evaluate(mandate, intent, ctx_zero())
    if d.verdict == "ALLOW":
        assert intent.total_minor <= mandate.bounds.max_total_minor


@given(mandate=mandates(), intent=intents())
def test_monotonic_in_amount(mandate, intent) -> None:
    """Raising the amount can never turn a DENY into an ALLOW."""
    hi = intent.model_copy(update={"total_minor": intent.total_minor + 1})
    if evaluate(mandate, intent, ctx_zero()).verdict == "DENY":
        assert evaluate(mandate, hi, ctx_zero()).verdict == "DENY"


@given(mandate=mandates(), intent=intents())
def test_deterministic(mandate, intent) -> None:
    a, b = evaluate(mandate, intent, ctx_zero()), evaluate(mandate, intent, ctx_zero())
    assert jcs(a.model_dump(mode="json", exclude={"decision_id"})) == jcs(
        b.model_dump(mode="json", exclude={"decision_id"})
    )


@given(mandate=mandates(), intent=intents(), ctx=contexts())
def test_total_function(mandate, intent, ctx) -> None:
    """No input combination raises — a crash in the engine is a security bug."""
    assert evaluate(mandate, intent, ctx).verdict in {"ALLOW", "DENY"}
